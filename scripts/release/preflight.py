"""Read-only host compatibility report, runnable with Astra's system Python 3.7+."""

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from admin import Parser, say
from host import local_engine


def command(*args):
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("PREFLIGHT_FAILED")
    return result.stdout.decode().strip()


def main():
    parser = Parser(add_help=False)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--backend-image")
    parser.add_argument("--node-image")
    args = parser.parse_args()
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            release[key] = value.strip('"')
    docker = local_engine()
    compose = command("docker", "compose", "version", "--short")
    up_help = command("docker", "compose", "up", "--help")
    run_help = command("docker", "compose", "run", "--help")
    report = {
        "os": release.get("PRETTY_NAME"),
        "os_id": release.get("ID"),
        "os_version": release.get("VERSION_ID"),
        "astra_version": Path("/etc/astra_version").read_text().strip()
        if Path("/etc/astra_version").exists()
        else None,
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "docker": docker.get("ServerVersion"),
        "docker_os": docker.get("OSType"),
        "docker_architecture": docker.get("Architecture"),
        "storage_driver": docker.get("Driver"),
        "cgroup_version": docker.get("CgroupVersion"),
        "security_options": docker.get("SecurityOptions", []),
        "compose": compose,
        "iproute_available": bool(shutil.which("ip")),
        "free_bytes": shutil.disk_usage(args.output.resolve().parent).free,
        "checks": {},
    }
    report["checks"] = {
        "python_compatible": sys.version_info >= (3, 7),
        "architecture_compatible": platform.machine() in ("x86_64", "amd64")
        and docker.get("Architecture") in ("x86_64", "amd64"),
        "linux_engine": docker.get("OSType") == "linux",
        "compose_wait": "--wait-timeout" in up_help,
        "compose_no_pull": "--pull" in up_help and "--pull" in run_help,
        "iproute_available": report["iproute_available"],
    }
    smoke = {}
    common = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=16m",
    ]
    if args.backend_image:
        code = """import threading, tempfile, os, fcntl
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
thread = threading.Thread(target=lambda: None)
thread.start(); thread.join()
hasher = PasswordHasher()
assert hasher.verify(hasher.hash('compatibility-test'), 'compatibility-test')
AESGCM(AESGCM.generate_key(bit_length=256)).encrypt(os.urandom(12), b'test', None)
with tempfile.TemporaryFile() as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    handle.write(b'test'); handle.flush(); os.fsync(handle.fileno())
"""
        smoke["backend"] = (
            subprocess.run(
                common + ["--entrypoint=python", args.backend_image, "-c", code],
                capture_output=True,
                timeout=90,
                check=False,
            ).returncode
            == 0
        )
    if args.node_image:
        code = (
            "const { Worker } = require('node:worker_threads');"
            "const w = new Worker('process.exit(0)', { eval:true });"
            "w.on('error', () => process.exit(1));"
        )
        smoke["node_threads"] = (
            subprocess.run(
                common + ["--user=10001:10001", "--entrypoint=node", args.node_image, "-e", code],
                capture_output=True,
                timeout=90,
                check=False,
            ).returncode
            == 0
        )
    report["container_smoke"] = smoke
    report["checks"].update(smoke)
    # Version evidence is reported, not turned into a false certification of Astra.
    report["astra_runtime_verified"] = False
    report["ready_for_trial"] = all(report["checks"].values())
    with args.output.open("x") as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
    say("preflightDone")
    if not report["ready_for_trial"]:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - restore safely or show a sanitized CLI error
        say("preflightFailed")
        sys.exit(1)
