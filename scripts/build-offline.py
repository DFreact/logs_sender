"""Export already-tested local images and a self-contained operations kit."""

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "release"))
from admin import (
    MESSAGES,
    Failure,
    Parser,
    digest,
    run,
    say,
    seal,
    write_json,
)


def build(destination):
    os.umask(0o077)
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    config = json.loads(
        run(
            [
                "docker",
                "compose",
                "-f",
                str(ROOT / "infra/compose/compose.yaml"),
                "config",
                "--no-interpolate",
                "--format",
                "json",
            ]
        )
    )
    config.pop("name", None)
    # All volume/network/secret names must be scoped by the destination project.
    for group in ("volumes", "networks", "secrets"):
        for name, value in config[group].items():
            value.pop("name", None)
            if group == "secrets":
                value["file"] = f"./.secrets/{name}"
    # The bootstrap server listens only on a Unix socket. TCP readiness avoids
    # mistaking it for the final server during a new volume's initialization.
    config["services"]["postgres"]["healthcheck"]["test"] = [
        "CMD",
        "pg_isready",
        "-h",
        "127.0.0.1",
        "-U",
        "eventhub",
        "-d",
        "eventhub",
    ]
    source_images = sorted({item["image"] for item in config["services"].values()})
    aliases, images = {}, {}
    for source in source_images:
        current = json.loads(run(["docker", "image", "inspect", source]))[0]
        # Content-derived local tag survives docker save/load, including base images
        # originally referred to by registry digests (not preserved by every loader).
        alias = "eventhub-offline:" + current["Id"].removeprefix("sha256:")
        run(["docker", "image", "tag", source, alias])
        aliases[source] = alias
        images[alias] = {
            "id": current["Id"],
            "architecture": current["Architecture"],
            "os": current["Os"],
        }
    for service in config["services"].values():
        service.pop("build", None)
        service["image"] = aliases[service["image"]]
        service["pull_policy"] = "never"
        if "healthcheck" in service:
            service["restart"] = "unless-stopped"
    write_json(destination / "compose.json", config)
    for name in (
        "admin.py",
        "upgrade.py",
        "storage.py",
        "messages.ru.json",
        "host.py",
        "configuration.py",
        "install.py",
        "install.sh",
        "preflight.py",
    ):
        shutil.copyfile(ROOT / "scripts/release" / name, destination / name)
    (destination / "operations.md").write_text(
        (ROOT / "docs/offline-operations.md").read_text()
    )
    for document in ('database-privileges.md', 'security-dependencies-0.12.1.json', 'security-review.md', 'astra-linux.md', 'security-dependencies-final.json', 'security-dependencies-before-os-update.json', 'security-os-advisories.json'):
        shutil.copyfile(ROOT / "docs" / document, destination / document)
    shutil.copyfile(ROOT / ".env.example", destination / "settings.env.example")
    (destination / "install.sh").chmod(0o755)
    run(
        [
            "docker",
            "image",
            "save",
            "--output",
            str(destination / "images.tar"),
            *sorted(images),
        ]
    )
    seal(
        destination,
        {
            "format": 1,
            "kind": "release",
            "release": "0.12.2-offline.2",
            "schema": "0012",
            "created_at": datetime.now(UTC).isoformat(),
            "images": images,
        },
        {item.name for item in destination.iterdir()},
    )
    say("packed")
    print(digest(destination / "manifest.json"))


if __name__ == "__main__":
    parser = Parser(
        description=MESSAGES["packDescription"],
        add_help=False,
        usage=MESSAGES["usage"] + MESSAGES["buildUsage"],
    )
    parser._optionals.title = MESSAGES["options"]
    parser.add_argument("--help", action="help", help=MESSAGES["packDescription"])
    parser.add_argument("--output", required=True, type=Path, metavar=MESSAGES["path"])
    try:
        args = parser.parse_args()
        build(args.output)
    except Failure as error:
        say(str(error))
        sys.exit(1)
    except Exception:  # noqa: BLE001 - CLI boundary must not expose secrets
        say("failed")
        sys.exit(1)
