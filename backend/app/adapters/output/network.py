"""Explicit egress allowlist, all-answer validation and IP-pinned sockets."""

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from app.api.errors import ApiFailure, ErrorCode


def blocked():
    raise ApiFailure(422, ErrorCode.OUTBOUND_BLOCKED)


def host_name(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 253
        or any(c in value for c in "%/\\@\r\n\t ")
    ):
        blocked()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        value = value.encode("idna").decode().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value) or ".." in value:
            blocked()
        return value


def endpoint(url):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
        ):
            blocked()
        if any(ord(c) < 33 or ord(c) > 126 for c in url) or "\\" in url:
            blocked()
        return host_name(parsed.hostname), parsed.port or 443, parsed.path or "/"
    except (ValueError, TypeError, UnicodeError):
        blocked()


def check_address(address, networks):
    ip = ipaddress.ip_address(address)
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        blocked()
    if ip.version == 6 and (
        ip.ipv4_mapped
        or ip.sixtofour
        or ip.teredo
        or ip in ipaddress.ip_network("64:ff9b::/96")
        or ip in ipaddress.ip_network("64:ff9b:1::/48")
    ):
        blocked()
    if not any(ip in ipaddress.ip_network(n) for n in networks):
        blocked()
    return str(ip)


def resolve(host, port):
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("resolve.py")), host, str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=True,
            env={"PATH": os.defpath},
            cwd="/tmp",
        )
        addresses = json.loads(result.stdout)
        if not addresses or len(addresses) > 16:
            blocked()
        return addresses
    except (ValueError, OSError, subprocess.SubprocessError):
        raise ApiFailure(422, ErrorCode.CHANNEL_CONNECTION_FAILED) from None


def validate_destination(settings, host, port):
    host = host_name(host)
    if (
        host not in [host_name(v) for v in settings.outbound_hosts]
        or port not in settings.outbound_ports
    ):
        blocked()
    addresses = resolve(host, port)
    checked = [check_address(address, settings.outbound_networks) for address in addresses]
    # Public Internet destinations are restricted even if an operator accidentally
    # configures a broad network allowlist. Private corporate endpoints remain explicit.
    if any(ipaddress.ip_address(address).is_global for address in checked) and (
        host != "platform-api2.max.ru" or port != 443 or not settings.max_allowed
    ):
        blocked()
    return checked


def connect(settings, host, port):
    # Resolve again for every connection; never give the host to create_connection.
    address = validate_destination(settings, host, port)[0]
    sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect((address, port))
        peer = check_address(sock.getpeername()[0], settings.outbound_networks)
        if peer != address:
            blocked()
        return sock
    except BaseException:
        sock.close()
        raise
