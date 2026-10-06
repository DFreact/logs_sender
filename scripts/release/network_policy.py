"""Generate scoped nftables rules and Docker settings; never change a host firewall."""

import hashlib
import ipaddress
import json
import re

MAX_HOST = "platform-api2.max.ru"


def policy(project, subnet, max_addresses):
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,50}", project):
        raise ValueError("INVALID_PROJECT")
    net = ipaddress.ip_network(subnet, strict=True)
    if net.version != 4 or not net.is_private or not 16 <= net.prefixlen <= 27:
        raise ValueError("INVALID_SUBNET")
    addresses = sorted({str(ipaddress.IPv4Address(value)) for value in max_addresses})
    if len(addresses) > 16 or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("INVALID_MAX_ADDRESSES")
    label = hashlib.sha256(project.encode()).hexdigest()[:10]
    bridge, table = "eh" + label, "eventhub_" + label
    api, delivery = str(net.network_address + 10), str(net.network_address + 11)
    # Match the bridge, rather than only source IP: source-address spoofing must
    # not bypass the default deny. Replies to incoming corporate traffic are allowed.
    allow = ""
    if addresses:
        allow = (
            "ip saddr { "
            + api
            + ", "
            + delivery
            + " } ip daddr { "
            + ", ".join(addresses)
            + " } tcp dport 443 counter accept"
        )
    rules = (
        """table inet TABLE {
    chain outbound {
        type filter hook forward priority -10; policy accept;
        iifname "BRIDGE" jump application;
    }
    chain application {
        oifname "BRIDGE" ip daddr SUBNET counter accept;
        ct direction reply ct state established,related counter accept;
        ALLOW
        counter drop;
    }
    chain host_input {
        type filter hook input priority -10; policy accept;
        iifname "BRIDGE" ct direction reply ct state established,related counter accept;
        iifname "BRIDGE" counter drop;
    }
}
""".replace("TABLE", table)
        .replace("BRIDGE", bridge)
        .replace("SUBNET", str(net))
        .replace("ALLOW", allow)
    )
    services = {}
    for name in (
        "postgres",
        "redis",
        "migrate",
        "db-init",
        "db-restore",
        "storage-init",
        "api",
        "web",
        "smtp",
        "worker",
        "ingestion",
        "delivery",
        "scheduler",
        "dispatcher",
    ):
        item = {
            "dns": ["127.0.0.1"],
            "dns_search": ["."],
            "sysctls": {"net.ipv6.conf.all.disable_ipv6": "1"},
            "networks": {"default": {}},
            "restart": "no",
        }
        # Explicit start through the guarded launcher after host boot. This avoids
        # containers restarting before firewall policy has been installed.
        if name in ("api", "delivery"):
            item["networks"]["default"]["ipv4_address"] = api if name == "api" else delivery
        if name not in ("postgres", "redis", "web", "smtp"):
            item["environment"] = {
                "HUB_MAX_ALLOWED": str(bool(addresses)).lower(),
                "HUB_TELEGRAM_ALLOWED": "false",
                "HUB_OUTBOUND_HOSTS": json.dumps([MAX_HOST] if addresses else []),
                "HUB_OUTBOUND_NETWORKS": json.dumps([a + "/32" for a in addresses]),
                "HUB_OUTBOUND_PORTS": "[443]",
            }
        if addresses:
            item["extra_hosts"] = [MAX_HOST + ":" + address for address in addresses]
        services[name] = item
    override = {
        "services": services,
        "networks": {
            "default": {
                "internal": False,
                "enable_ipv6": False,
                "driver_opts": {"com.docker.network.bridge.name": bridge},
                "ipam": {"config": [{"subnet": str(net), "ip_range": str(list(net.subnets())[1])}]},
            }
        },
    }
    return (
        {
            "project": project,
            "bridge": bridge,
            "table": table,
            "max_host": MAX_HOST,
            "max_ipv4": addresses,
            "subnet": str(net),
        },
        override,
        rules,
    )
