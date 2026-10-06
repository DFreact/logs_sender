"""Apply only the named project's table, and verify live rules before startup."""

import hashlib
import json
import os
import subprocess

from network_policy import policy


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("NETWORK_POLICY_FAILED")
    return result.stdout


def normalized(value):
    if isinstance(value, dict):
        return {
            k: normalized(v)
            for k, v in value.items()
            if k not in ("handle", "packets", "bytes", "metainfo")
        }
    if isinstance(value, list):
        return [normalized(v) for v in value if not (isinstance(v, dict) and "metainfo" in v)]
    return value


def live(table):
    return normalized(json.loads(run(["nft", "--json", "list", "table", "inet", table])))


def load(directory):
    metadata = json.loads((directory / "policy.json").read_text())
    generated, config, rules = policy(metadata["project"], metadata["subnet"], metadata["max_ipv4"])
    if metadata != generated or (directory / "outbound.nft").read_text() != rules:
        raise ValueError("NETWORK_POLICY_CHANGED")
    return generated, config, rules


def apply(directory):
    metadata, _, rules = load(directory)
    listed = json.loads(run(["nft", "--json", "list", "tables"]))
    exists = any(
        item.get("table", {}).get("name") == metadata["table"]
        and item.get("table", {}).get("family") == "inet"
        for item in listed["nftables"]
    )
    prefix = "delete table inet " + metadata["table"] + "\n" if exists else ""
    transaction = (prefix + rules).encode()
    run(["nft", "--check", "--file", "-"], transaction)
    # A single netlink batch removes and replaces our own table atomically.
    run(["nft", "--file", "-"], transaction)
    receipt = {
        "rules_sha256": hashlib.sha256(rules.encode()).hexdigest(),
        "live": live(metadata["table"]),
    }
    path = directory / "installed.json"
    path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    path.chmod(0o600)


def check(directory):
    metadata, _, rules = load(directory)
    receipt = json.loads((directory / "installed.json").read_text())
    if receipt["rules_sha256"] != hashlib.sha256(rules.encode()).hexdigest() or receipt[
        "live"
    ] != live(metadata["table"]):
        raise ValueError("NETWORK_POLICY_MISSING_OR_CHANGED")
    return metadata


def local_engine():
    """A local firewall cannot protect a remote or rootless Docker daemon."""
    if os.environ.get("DOCKER_CONTEXT"):
        context = os.environ["DOCKER_CONTEXT"]
        endpoint = json.loads(run(["docker", "context", "inspect", context]))[0]["Endpoints"][
            "docker"
        ]["Host"]
    elif os.environ.get("DOCKER_HOST"):
        endpoint = os.environ["DOCKER_HOST"]
    else:
        context = run(["docker", "context", "show"]).decode().strip()
        endpoint = json.loads(run(["docker", "context", "inspect", context]))[0]["Endpoints"][
            "docker"
        ]["Host"]
    if not endpoint.startswith("unix:///"):
        raise ValueError("LOCAL_DOCKER_REQUIRED")
    info = json.loads(run(["docker", "info", "--format", "{{json .}}"]))
    if info.get("OSType") != "linux" or any(
        "rootless" in item for item in info.get("SecurityOptions", [])
    ):
        raise ValueError("ROOTFUL_LINUX_DOCKER_REQUIRED")
    return info
