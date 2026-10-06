"""Exercise actual nftables packets in user/network namespaces, never on the host."""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
import network_guard  # noqa: E402
from network_policy import policy  # noqa: E402


def cmd(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def inside(pid, *args):
    return cmd("nsenter", "-t", str(pid), "-n", *args)


SERVER = r"""
import socket,threading,time

def serve(port,udp=False):
    sock=socket.socket(type=socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
    sock.bind(('8.8.8.8' if udp else '0.0.0.0',port))
    if not udp: sock.listen()
    while True:
        if udp:
            data,peer=sock.recvfrom(100)
            sock.sendto(b'ok',peer)
        else:
            peer,_=sock.accept()
            peer.sendall(b'ok')
            peer.close()
for port in (443,80,53): threading.Thread(target=serve,args=(port,),daemon=True).start()
threading.Thread(target=serve,args=(53,True),daemon=True).start()
def ipv6():
    sock=socket.socket(socket.AF_INET6)
    sock.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
    sock.bind(('::',443));sock.listen()
    while True:
        peer,_=sock.accept();peer.sendall(b'ok');peer.close()
threading.Thread(target=ipv6,daemon=True).start()
time.sleep(180)
"""

PROBE = r"""
import socket,json
cases=[('MAX', '8.8.8.8',443,False,None),
       ('other_host','1.1.1.1',443,False,None),
       ('wrong_port','8.8.8.8',80,False,None),
       ('dns_tcp','8.8.8.8',53,False,None),
       ('dns_udp','8.8.8.8',53,True,None),
       ('worker','8.8.8.8',443,False,'172.31.62.20'),
       ('host','172.31.62.1',12345,False,None),
       ('ipv6','2001:db8::1',443,False,None)]
result={}
for name,host,port,udp,source in cases:
    sock=socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, type=socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
    sock.settimeout(0.5)
    if source: sock.bind((source,0))
    try:
        sock.connect((host,port))
        if udp: sock.send(b'probe')
        result[name]=sock.recv(2)==b'ok'
    except OSError: result[name]=False
    finally: sock.close()
print(json.dumps(result))
"""


def main(output):
    processes = []
    with tempfile.TemporaryDirectory(
        prefix="network-fixture-", dir=ROOT / ".local"
    ) as temp:
        directory = Path(temp)
        metadata, config, rules = policy(
            "eventhub-network-test", "172.31.62.0/24", ["8.8.8.8"]
        )
        (directory / "policy.json").write_text(json.dumps(metadata))
        (directory / "outbound.nft").write_text(rules)
        try:
            for _ in range(2):
                processes.append(subprocess.Popen(["unshare", "--net", "sleep", "180"]))
            client, server = [p.pid for p in processes]
            for pid in (client, server):
                deadline = time.monotonic() + 3
                while os.readlink("/proc/self/ns/net") == os.readlink(
                    "/proc/" + str(pid) + "/ns/net"
                ):
                    if time.monotonic() > deadline:
                        raise RuntimeError("NAMESPACE_TIMEOUT")
                    time.sleep(0.01)
            br = metadata["bridge"]
            cmd("ip", "link", "add", br, "type", "bridge")
            cmd("ip", "link", "set", br, "up")
            for parent, peer, pid, address in [
                ("bridgeport", "client", client, "172.31.62.1/24"),
                ("wan", "server", server, "10.200.0.1/24"),
            ]:
                cmd("ip", "link", "add", parent, "type", "veth", "peer", "name", peer)
                cmd("ip", "link", "set", peer, "netns", str(pid))
                if parent == "bridgeport":
                    cmd("ip", "link", "set", parent, "master", br)
                cmd("ip", "addr", "add", address, "dev", br if parent == "bridgeport" else parent)
                cmd("ip", "link", "set", parent, "up")
                inside(pid, "ip", "link", "set", "lo", "up")
                inside(pid, "ip", "link", "set", peer, "up")
            inside(client, "ip", "addr", "add", "172.31.62.10/24", "dev", "client")
            inside(client, "ip", "addr", "add", "172.31.62.20/24", "dev", "client")
            inside(client, "ip", "route", "add", "default", "via", "172.31.62.1")
            inside(server, "ip", "addr", "add", "10.200.0.2/24", "dev", "server")
            inside(server, "ip", "route", "add", "default", "via", "10.200.0.1")
            for address in ("8.8.8.8", "1.1.1.1"):
                inside(server, "ip", "addr", "add", address + "/32", "dev", "lo")
                cmd("ip", "route", "add", address + "/32", "via", "10.200.0.2")
            Path("/proc/sys/net/ipv4/ip_forward").write_text("1")
            cmd("ip", "-6", "addr", "add", "fd00:1::1/64", "dev", br, "nodad")
            cmd("ip", "-6", "addr", "add", "fd00:2::1/64", "dev", "wan", "nodad")
            inside(client, "ip", "-6", "addr", "add", "fd00:1::10/64", "dev", "client", "nodad")
            inside(client, "ip", "-6", "route", "add", "default", "via", "fd00:1::1")
            inside(server, "ip", "-6", "addr", "add", "fd00:2::2/64", "dev", "server", "nodad")
            inside(server, "ip", "-6", "addr", "add", "2001:db8::1/128", "dev", "lo", "nodad")
            inside(server, "ip", "-6", "route", "add", "default", "via", "fd00:2::1")
            cmd("ip", "-6", "route", "add", "2001:db8::1/128", "via", "fd00:2::2")
            Path("/proc/sys/net/ipv6/conf/all/forwarding").write_text("1")
            processes.append(
                subprocess.Popen(
                    ["nsenter", "-t", str(server), "-n", sys.executable, "-c", SERVER]
                )
            )
            listener = socket.socket()
            listener.bind(("0.0.0.0", 12345))
            listener.listen()

            def accept():
                while True:
                    peer, _ = listener.accept()
                    peer.sendall(b"ok")
                    peer.close()

            Thread(target=accept, daemon=True).start()
            time.sleep(1.5)  # Let IPv6 link-local duplicate-address detection finish.
            # Warm neighbor discovery before the deliberately short negative probes.
            inside(client, sys.executable, "-c", "import socket;s=socket.socket(socket.AF_INET6);s.settimeout(3);s.connect(('2001:db8::1',443));assert s.recv(2)==b'ok'")
            before = json.loads(inside(client, sys.executable, "-c", PROBE))
            assert all(before.values()), before
            network_guard.apply(directory)
            after = json.loads(inside(client, sys.executable, "-c", PROBE))
            assert after == {k: k == "MAX" for k in before}, after
            network_guard.check(directory)
            # Removal is detected; a guarded start cannot silently continue.
            cmd("nft", "delete", "table", "inet", metadata["table"])
            try:
                network_guard.check(directory)
            except RuntimeError:
                pass
            else:
                raise AssertionError("MISSING_POLICY_ACCEPTED")
            report = {
                "namespace_isolated": True,
                "linux_bridge": True,
                "synthetic_destinations_only": True,
                "baseline": before,
                "protected": after,
                "removed_policy_rejected": True,
                "external_dns_disabled": all(
                    v["dns"] == ["127.0.0.1"] for v in config["services"].values()
                ),
            }
            with output.open("x") as target:
                json.dump(report, target, ensure_ascii=False, indent=2)
            print(
                "Сетевые ограничения: разрешён только заданный адрес MAX и порт 443; остальные проверки блокировки пройдены."
            )
        finally:
            for process in processes[::-1]:
                process.terminate()
            for process in processes:
                process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.output.resolve())
