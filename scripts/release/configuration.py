"""Change ports, DNS and Docker subnet without removing persistent volumes."""
import ipaddress
import json
import os
import shutil
import tempfile
from pathlib import Path

import admin
from host import choose_subnet


def values(path):
    return {k.strip(): v.strip() for line in path.read_text().splitlines()
            for k, sep, v in [line.partition("=")] if sep and not k.lstrip().startswith("#")}


def replace(path, data):
    temporary = path.with_name(path.name + ".next")
    with temporary.open("w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def configure(args):
    directory = args.directory.resolve()
    state = json.loads((directory / "installation.json").read_text())
    if state.get("network_management") != "external" or not state.get("ready"):
        raise admin.Failure("legacyInstallation")
    admin.network_check(directory)
    old_settings = (directory / "settings.env").read_text()
    current = values(directory / "settings.env")
    try:
        network = choose_subnet(args.subnet or current["HUB_DOCKER_SUBNET"], state["project"])
        http = args.http_port if args.http_port is not None else int(current["HUB_HTTP_PORT"])
        smtp = args.smtp_port if args.smtp_port is not None else int(current["HUB_SMTP_PORT"])
        if not all(1024 <= x <= 65535 for x in (http, smtp)) or http == smtp:
            raise ValueError()
        for item in args.dns:
            address = ipaddress.ip_address(item)
            if address.is_loopback or address.is_unspecified or address.is_multicast:
                raise ValueError()
    except (ValueError, RuntimeError, OSError, KeyError):
        raise admin.Failure("subnetUnavailable") from None
    updates = {"HUB_DOCKER_SUBNET": network, "HUB_HTTP_PORT": str(http), "HUB_SMTP_PORT": str(smtp)}
    lines = [line for line in old_settings.splitlines() if line.partition("=")[0].strip() not in updates]
    new_settings = "\n".join(lines + [k + "=" + v for k, v in updates.items()]) + "\n"
    config = json.loads((directory / "compose.json").read_text())
    if args.dns:
        for service in config["services"].values():
            service["dns"] = args.dns
    # Validate candidate interpolation before stopping anything. No secrets in stdout.
    with tempfile.TemporaryDirectory(prefix=".configure-", dir=directory) as temporary:
        candidate = Path(temporary)
        (candidate / "settings.env").write_text(new_settings)
        (candidate / "compose.json").write_text(json.dumps(config))
        admin.run(["docker", "compose", "--project-name", state["project"],
                   "--env-file", str(candidate / "settings.env"),
                   "-f", str(candidate / "compose.json"), "config", "--quiet"],
                  env={k: v for k, v in os.environ.items() if not k.startswith(("HUB_", "COMPOSE_"))})
    snapshot = Path(tempfile.mkdtemp(prefix="configuration-previous-", dir=directory))
    for name in ("settings.env", "compose.json", "installation.json"):
        shutil.copyfile(directory / name, snapshot / name)
    try:
        # No --volumes: persistent database and blob volumes are retained.
        admin.compose(directory, "down", "--timeout", "60")
        replace(directory / "settings.env", new_settings)
        replace(directory / "compose.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        state["compose_sha256"] = admin.digest(directory / "compose.json")
        replace(directory / "installation.json", json.dumps(state, indent=2) + "\n")
        admin.up(directory)
    except Exception:  # noqa: BLE001 - restore safely or show a sanitized CLI error
        try:
            admin.compose(directory, "down", "--timeout", "60")
            for name in ("settings.env", "compose.json", "installation.json"):
                replace(directory / name, (snapshot / name).read_text())
            admin.up(directory)
        except Exception:  # noqa: BLE001 - restore safely or show a sanitized CLI error
            raise admin.Failure("configureRollbackFailed") from None
        raise admin.Failure("configureRolledBack") from None
    admin.say("configured")
