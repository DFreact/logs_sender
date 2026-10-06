"""Generate loopback-only user systemd socket proxies for an isolated local install."""

import argparse
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path


def generate(directory, label, http_port, smtp_port):
    if not re.fullmatch(r"eventhub-[a-z0-9-]+", label):
        raise ValueError("INVALID_LABEL")
    if http_port == smtp_port or not all(
        1024 <= p <= 65535 for p in (http_port, smtp_port)
    ):
        raise ValueError("INVALID_PORT")
    state = json.loads((directory / "installation.json").read_text())
    config_bytes = (directory / "compose.json").read_bytes()
    if hashlib.sha256(config_bytes).hexdigest() != state["compose_sha256"]:
        raise ValueError("INSTALLATION_CHANGED")
    config = json.loads(config_bytes)
    if (
        not state["isolated"]
        or not state["ready"]
        or not config["networks"]["default"]["internal"]
    ):
        raise ValueError("ISOLATED_READY_INSTALLATION_REQUIRED")
    units = directory / "local-access"
    units.mkdir(mode=0o700, exist_ok=False)
    for name, service, port, target_port in (
        ("http", "web", http_port, 8080),
        ("smtp", "smtp", smtp_port, 2525),
    ):
        address = ipaddress.IPv4Address(
            config["services"][service]["networks"]["default"]["ipv4_address"]
        )
        if not address.is_private:
            raise ValueError("PRIVATE_DESTINATION_REQUIRED")
        unit = label + "-" + name
        (units / (unit + ".socket")).write_text(
            "[Unit]\nDescription=EventHub local " + name + "\n"
            "[Socket]\nListenStream=127.0.0.1:" + str(port) + "\nNoDelay=true\n"
            "[Install]\nWantedBy=sockets.target\n"
        )
        (units / (unit + ".service")).write_text(
            "[Unit]\nDescription=EventHub local socket proxy\nRequires="
            + unit
            + ".socket\n"
            "After=" + unit + ".socket\n"
            "[Service]\nExecStart=/usr/lib/systemd/systemd-socket-proxyd "
            + str(address)
            + ":"
            + str(target_port)
            + "\n"
            "NoNewPrivileges=yes\nRestrictAddressFamilies=AF_INET AF_UNIX\n"
            "Restart=on-failure\nStandardOutput=null\n"
        )
    print(str(units))


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--smtp-port", type=int, required=True)
    args = parser.parse_args()
    generate(args.directory.resolve(), args.label, args.http_port, args.smtp_port)
