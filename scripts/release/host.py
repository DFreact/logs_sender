"""Local Docker and subnet checks, without managing the host firewall."""
import ipaddress
import json
import os
import shutil
import subprocess


def run(args):
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("HOST_CHECK_FAILED")
    return result.stdout


def local_engine():
    context = os.environ.get("DOCKER_CONTEXT")
    if context:
        endpoint = json.loads(run(["docker", "context", "inspect", context]))[0]["Endpoints"]["docker"]["Host"]
    elif os.environ.get("DOCKER_HOST"):
        endpoint = os.environ["DOCKER_HOST"]
    else:
        context = run(["docker", "context", "show"]).decode().strip()
        endpoint = json.loads(run(["docker", "context", "inspect", context]))[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix:///"):
        raise ValueError("LOCAL_DOCKER_REQUIRED")
    info = json.loads(run(["docker", "info", "--format", "{{json .}} "]))
    if info.get("OSType") != "linux" or any("rootless" in x for x in info.get("SecurityOptions", [])):
        raise ValueError("ROOTFUL_LINUX_DOCKER_REQUIRED")
    return info


def subnet(value):
    network = ipaddress.ip_network(value, strict=True)
    ranges = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if network.version != 4 or not 16 <= network.prefixlen <= 27 or not any(network.subnet_of(n) for n in ranges):
        raise ValueError("INVALID_SUBNET")
    return network


def occupied(exclude_project=None):
    ids = run(["docker", "network", "ls", "-q"]).decode().split()
    networks = json.loads(run(["docker", "network", "inspect", *ids])) if ids else []
    result, excluded_bridges = [], set()
    for item in networks:
        if exclude_project and (item.get("Labels") or {}).get("com.docker.compose.project") == exclude_project:
            excluded_bridges.add((item.get("Options") or {}).get("com.docker.network.bridge.name", "br-" + item["Id"][:12]))
            continue
        for entry in (item.get("IPAM", {}).get("Config") or []):
            if entry.get("Subnet"):
                net = ipaddress.ip_network(entry["Subnet"])
                if net.version == 4:
                    result.append(net)
    if not shutil.which("ip"):
        raise ValueError("IPROUTE_REQUIRED")
    routes = json.loads(run(["ip", "-j", "-4", "route", "show", "table", "all"]))
    for route in routes:
        if route.get("dev") in excluded_bridges or route.get("dst", "default") == "default":
            continue
        net = ipaddress.ip_network(route["dst"], strict=False)
        if net.version == 4:
            result.append(net)
    return result


def choose_subnet(value=None, exclude_project=None):
    used = occupied(exclude_project)
    candidates = [value] if value else (
        [f"172.{b}.{c}.0/24" for b in (31, 30, 29) for c in range(54, 255)]
        + [f"10.{b}.{c}.0/24" for b in (250, 251, 252) for c in range(54, 255)]
        + [f"192.168.{c}.0/24" for c in range(54, 255)]
    )
    for candidate in candidates:
        network = subnet(candidate)
        if not any(network.overlaps(other) for other in used):
            return str(network)
    raise ValueError("SUBNET_CONFLICT")
