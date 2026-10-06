"""Offline operations. Only Python's standard library and Docker Compose are required."""

import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MESSAGES = json.loads((HERE / "messages.ru.json").read_text())
WRITERS = (
    "web",
    "smtp",
    "api",
    "dispatcher",
    "scheduler",
    "ingestion",
    "delivery",
    "worker",
)
SECRET_NAMES = (
    "db_password",
    "db_admin_password",
    "db_migration_password",
    "redis_password",
    "auth_secret",
    "channel_secret",
)
BACKUP_FILES = {"database.dump", "blobs.tar.gz", "channel_secret", "settings.env"}


class Failure(Exception):
    pass


def say(key):
    print(MESSAGES[key], flush=True)


def run(command, *, stdin=None, stdout=None, input=None, cwd=None, env=None):
    result = subprocess.run(
        command,
        stdin=stdin,
        stdout=stdout or subprocess.PIPE,
        stderr=subprocess.PIPE,
        input=input,
        cwd=cwd,
        env=env,
        check=False,
    )
    if result.returncode:
        # Never echo a command, child exception, SQL, credentials or a subprocess log.
        raise Failure("failed")
    return result.stdout


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, data):
    with path.open("x", encoding="utf-8") as target:
        json.dump(data, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())


def seal(directory, data, names):
    for name in names:
        with (directory / name).open("rb") as source:
            os.fsync(source.fileno())
    data["files"] = {name: digest(directory / name) for name in sorted(names)}
    write_json(directory / "manifest.json", data)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verified(directory, kind):
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["format"] != 1 or manifest["kind"] != kind:
            raise ValueError()
        names = manifest["files"]
        expected = (
            BACKUP_FILES
            if kind == "backup"
            else {
                "images.tar",
                "compose.json",
                "admin.py",
                "upgrade.py",
                "storage.py",
                "messages.ru.json",
                "operations.md",
                "security-review.md",
                "database-privileges.md",
                "security-dependencies-0.12.1.json",
                "astra-linux.md",
                "security-dependencies-final.json",
                "security-dependencies-before-os-update.json",
                "security-os-advisories.json",
                "settings.env.example",
                "host.py",
                "configuration.py",
                "preflight.py",
                "install.py",
                "install.sh",
            }
        )
        if set(names) != expected:
            raise ValueError()
        for name, checksum in names.items():
            path = directory / name
            if path.is_symlink() or not path.is_file() or digest(path) != checksum:
                raise ValueError()
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        raise Failure("damaged") from None


def images(manifest):
    for name, expected in manifest["images"].items():
        try:
            current = json.loads(run(["docker", "image", "inspect", name]))[0]
        except Failure:
            raise Failure("images") from None
        if current["Id"] != expected["id"] or current["Architecture"] != expected["architecture"]:
            raise Failure("images")


def compose(directory, *args, **kwargs):
    state = json.loads((directory / "installation.json").read_text())
    # Explicit env file and cwd prevent an unrelated shell's .env from being read.
    return run(
        [
            "docker",
            "compose",
            "--project-name",
            state["project"],
            "--env-file",
            str(directory / "settings.env"),
            "-f",
            str(directory / "compose.json"),
            *args,
        ],
        cwd=directory,
        env={k: v for k, v in os.environ.items() if not k.startswith(("HUB_", "COMPOSE_"))},
        **kwargs,
    )


@contextlib.contextmanager
def lock(directory):
    with (directory / ".operation.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("busy") from None
        yield


def prepare(args, manifest, channel=None, settings=None):
    directory = args.directory.resolve()
    ca = getattr(args, "ca_file", None)
    if ca:
        try:
            if ca.stat().st_size > 1024 * 1024 or b"PRIVATE KEY" in ca.read_bytes():
                raise ValueError()
            ssl.create_default_context(cafile=str(ca))
        except (OSError, ValueError, ssl.SSLError):
            raise Failure("invalid") from None
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,50}", args.project):
        raise Failure("invalid")
    if (
        not all(1024 <= port <= 65535 for port in (args.http_port, args.smtp_port))
        or args.http_port == args.smtp_port
    ):
        raise Failure("invalid")
    try:
        from host import subnet
        subnet(args.subnet)
        for value in getattr(args, "dns", None) or []:
            address = ipaddress.ip_address(value)
            if address.is_loopback or address.is_unspecified or address.is_multicast:
                raise ValueError()
    except ValueError:
        raise Failure("invalid") from None
    for resource in ("container", "volume", "network"):
        if run(
            [
                "docker",
                resource,
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={args.project}",
            ]
        ).strip():
            raise Failure("exists")
    # Also reject unlabelled resources with the exact names Compose would use.
    for name in ("database", "blobs"):
        result = subprocess.run(
            ["docker", "volume", "inspect", f"{args.project}_{name}"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            raise Failure("exists")
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    except FileExistsError:
        raise Failure("exists") from None
    (directory / ".secrets").mkdir(mode=0o700)
    for name in SECRET_NAMES:
        value = (
            channel
            if name == "channel_secret" and channel
            else (secrets.token_urlsafe(48) + "\n").encode()
        )
        path = directory / ".secrets" / name
        path.write_bytes(value)
        path.chmod(0o444)  # Private 0700 parent; mounted by non-root services.
    config = json.loads((HERE / "compose.json").read_text())
    config["networks"]["default"]["internal"] = args.isolated
    config["networks"]["default"]["enable_ipv6"] = False
    config["networks"]["default"]["ipam"] = {
        "config": [{"subnet": "${HUB_DOCKER_SUBNET}"}]
    }
    for service in config["services"].values():
        service["sysctls"] = {"net.ipv6.conf.all.disable_ipv6": "1"}
        if "healthcheck" in service:
            service["restart"] = "unless-stopped"
        if getattr(args, "dns", None):
            service["dns"] = args.dns
    if args.isolated:
        net = ipaddress.ip_network(args.subnet, strict=True)
        config["networks"]["default"]["ipam"]["config"][0]["ip_range"] = str(list(net.subnets())[1])
        for name, offset in (("web", 12), ("smtp", 13)):
            config["services"][name]["networks"] = {
                "default": {"ipv4_address": str(net.network_address + offset)}
            }
            config["services"][name].pop("ports", None)
        for service in config["services"].values():
            service["dns"] = ["127.0.0.1"]
            service["dns_search"] = ["."]
    if ca:
        target = directory / "trusted-ca.pem"
        shutil.copyfile(ca, target)
        target.chmod(0o444)
        for name in ("api", "delivery"):
            config["services"][name].setdefault("volumes", []).append(
                {
                    "type": "bind",
                    "source": str(target),
                    "target": "/run/trusted-ca.pem",
                    "read_only": True,
                }
            )
            config["services"][name]["environment"]["HUB_OUTBOUND_CA_FILE"] = "/run/trusted-ca.pem"
    write_json(directory / "compose.json", config)
    # Restore non-secret operating settings; transport identity belongs to the new host.
    exclude = {"HUB_HTTP_PORT", "HUB_SMTP_PORT", "HUB_DOCKER_SUBNET"}
    original = settings or (HERE / "settings.env.example").read_text()
    lines = [
        line for line in original.splitlines() if line.partition("=")[0].strip() not in exclude
    ]
    lines += [
        f"HUB_HTTP_PORT={args.http_port}",
        f"HUB_SMTP_PORT={args.smtp_port}",
        f"HUB_DOCKER_SUBNET={args.subnet}",
    ]
    (directory / "settings.env").write_text("\n".join(lines) + "\n")
    write_json(
        directory / "installation.json",
        {
            "project": args.project,
            "release": manifest["release"],
            "schema": manifest["schema"],
            "images": manifest["images"],
            "ready": False,
            "isolated": args.isolated,
            "network_management": "isolated" if args.isolated else "external",
            "compose_sha256": digest(directory / "compose.json"),
            "ca_sha256": digest(directory / "trusted-ca.pem") if ca else None,
        },
    )
    return directory


def mark_ready(directory):
    state = json.loads((directory / "installation.json").read_text())
    state["ready"] = True
    write_json(directory / "installation.next.json", state)
    os.replace(directory / "installation.next.json", directory / "installation.json")


def network_check(directory):
    state = json.loads((directory / "installation.json").read_text())
    if digest(directory / "compose.json") != state.get("compose_sha256"):
        raise Failure("configurationChanged")
    if state.get("ca_sha256") and digest(directory / "trusted-ca.pem") != state["ca_sha256"]:
        raise Failure("configurationChanged")
    if state.get("network_policy"):
        # Older guarded installations must continue using their original kit.
        # Do not silently disable an already installed firewall policy.
        raise Failure("legacyInstallation")
    from host import local_engine
    local_engine()


def up(directory, *services):
    network_check(directory)
    compose(
        directory,
        "up",
        "-d",
        "--no-build",
        "--pull",
        "never",
        "--wait",
        "--wait-timeout",
        "180",
        *services,
    )


def storage(directory, action, **kwargs):
    script = (HERE / "storage.py").read_text()
    compose(
        directory,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--pull",
        "never",
        "api",
        "python",
        "-c",
        script,
        action,
        **kwargs,
    )


def backup(directory, output):
    state = json.loads((directory / "installation.json").read_text())
    if not state["ready"]:
        raise Failure("invalid")
    images(state)
    try:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        raise Failure("exists") from None
    began = time.monotonic()
    services = compose(directory, "ps", "--status", "running", "--services").decode().split()
    previous = [service for service in WRITERS if service in services]
    try:
        compose(directory, "stop", "--timeout", "60", *WRITERS)
        if set(
            compose(directory, "ps", "--status", "running", "--services").decode().split()
        ) & set(WRITERS):
            raise Failure("failed")
        checkpoint = datetime.now(timezone.utc).isoformat()
        with (output / "database.dump").open("xb") as target:
            compose(
                directory,
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "eventhub",
                "-d",
                "eventhub",
                "-Fc",
                stdout=target,
            )
        with (output / "database.dump").open("rb") as source:
            compose(
                directory,
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "--list",
                stdin=source,
            )
        with (output / "blobs.tar.gz").open("xb") as target:
            storage(directory, "save", stdout=target)
        shutil.copyfile(directory / ".secrets" / "channel_secret", output / "channel_secret")
        shutil.copyfile(directory / "settings.env", output / "settings.env")
        validate_archive(output / "blobs.tar.gz")
        seal(
            output,
            {
                "format": 1,
                "kind": "backup",
                "release": state["release"],
                "schema": state["schema"],
                "images": state["images"],
                "checkpoint": checkpoint,
                "seconds": round(time.monotonic() - began, 3),
            },
            BACKUP_FILES,
        )
    finally:
        if previous:
            try:
                network_check(directory)
                compose(directory, "start", "--wait", "--wait-timeout", "180", *previous)
            except Failure:
                raise Failure("resumeFailed") from None
    say("backup")


def validate_archive(path):
    from storage import valid

    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            if not (member.isdir() or member.isfile()) or not valid(member.name, member.isdir()):
                raise Failure("damaged")
            if member.isfile():
                with archive.extractfile(member) as source:
                    for _ in iter(lambda: source.read(1024 * 1024), b""):
                        pass
            archive.members.clear()


def restore(args, manifest):
    saved = verified(args.backup, "backup")
    compatible_release = saved["release"] == manifest["release"] or (
        saved["release"] == "0.12.2-offline.1" and manifest["release"] == "0.12.2-offline.2"
    )
    if not compatible_release or any(saved[key] != manifest[key] for key in ("schema", "images")):
        raise Failure("damaged")
    validate_archive(args.backup / "blobs.tar.gz")
    directory = prepare(
        args,
        manifest,
        (args.backup / "channel_secret").read_bytes(),
        (args.backup / "settings.env").read_text(),
    )
    with lock(directory):
        # No migrations or application writers until the dump has been restored.
        up(directory, "postgres")
        compose(directory, "run", "--rm", "--no-deps", "-T", "--pull", "never", "db-init")
        with (args.backup / "database.dump").open("rb") as source:
            compose(
                directory,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--pull",
                "never",
                "db-restore",
                stdin=source,
            )
        compose(
            directory,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--pull",
            "never",
            "migrate",
            "python",
            "-m",
            "app.persistence.migrate",
            "grants",
        )
        with (args.backup / "blobs.tar.gz").open("rb") as source:
            storage(directory, "restore", stdin=source)
        storage(directory, "check")
        # Rotate sessions on recovery; channel ciphertext remains decryptable.
        compose(
            directory,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "eventhub",
            "-d",
            "eventhub",
            "-c",
            "DELETE FROM sessions;",
        )
        revision = (
            compose(
                directory,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-At",
                "-U",
                "eventhub",
                "-d",
                "eventhub",
                "-c",
                "SELECT version_num FROM alembic_version;",
            )
            .decode()
            .strip()
        )
        if revision != manifest["schema"]:
            raise Failure("damaged")
        mark_ready(directory)
    say("restore")


class Parser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace("usage: ", "", 1)

    def format_usage(self):
        return super().format_usage().replace("usage: ", "", 1)

    def error(self, message):
        raise Failure("invalid")


def main():
    os.umask(0o077)
    parser = Parser(
        description=MESSAGES["description"],
        usage=MESSAGES["usage"] + MESSAGES["adminUsage"],
        add_help=False,
    )
    parser._positionals.title = MESSAGES["commands"]
    parser._optionals.title = MESSAGES["options"]
    parser.add_argument("--help", action="help", help=MESSAGES["description"])
    parser.add_argument(
        "command",
        choices=("verify", "load", "install", "start", "stop", "backup", "restore", "configure"),
    )
    parser.add_argument("--directory", type=Path, metavar=MESSAGES["path"])
    parser.add_argument("--project", default="eventhub", metavar=MESSAGES["project"])
    parser.add_argument("--http-port", type=int, metavar=MESSAGES["port"])
    parser.add_argument("--smtp-port", type=int, metavar=MESSAGES["port"])
    parser.add_argument("--subnet", metavar=MESSAGES["subnet"])
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--ca-file", type=Path, metavar=MESSAGES["path"])
    parser.add_argument("--dns", action="append", default=[])
    parser.add_argument("--backup", type=Path, metavar=MESSAGES["path"])
    parser.add_argument("--output", type=Path, metavar=MESSAGES["path"])
    args = parser.parse_args()
    manifest = verified(HERE, "release")
    if args.command == "verify":
        say("verified")
        return
    if args.command == "load":
        run(["docker", "image", "load", "--input", str(HERE / "images.tar")])
        images(manifest)
        say("loaded")
        return
    if (
        args.directory is None
        or (args.command == "backup" and args.output is None)
        or (args.command == "restore" and args.backup is None)
    ):
        raise Failure("invalid")
    images(manifest)
    if args.command in ("install", "restore"):
        args.http_port = args.http_port if args.http_port is not None else 8080
        args.smtp_port = args.smtp_port if args.smtp_port is not None else 2525
        try:
            from host import choose_subnet, local_engine
            local_engine()
            args.subnet = choose_subnet(args.subnet)
        except (ValueError, RuntimeError, OSError):
            raise Failure("subnetUnavailable") from None
    if args.command == "install":
        directory = prepare(args, manifest)
        with lock(directory):
            up(directory)
            mark_ready(directory)
        say("installed")
    elif args.command == "restore":
        restore(args, manifest)
    else:
        args.directory = args.directory.resolve()
        state = json.loads((args.directory / "installation.json").read_text())
        if state["images"] != manifest["images"] or not state["ready"]:
            raise Failure("damaged")
        with lock(args.directory):
            if args.command == "start":
                up(args.directory)
                say("started")
            elif args.command == "stop":
                compose(args.directory, "stop", "--timeout", "60")
                say("stopped")
            elif args.command == "configure":
                from configuration import configure
                configure(args)
            elif args.command == "backup":
                backup(args.directory, args.output.resolve())


if __name__ == "__main__":
    try:
        main()
    except Failure as error:
        say(str(error))
        sys.exit(1)
    except KeyboardInterrupt:
        say("interrupted")
        sys.exit(130)
    except Exception:  # noqa: BLE001 - CLI boundary must not expose secrets
        say("failed")
        sys.exit(1)
