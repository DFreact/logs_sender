"""Quiesced snapshot of this workspace's 0.11.0 development project only."""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
import admin
from upgrade import fingerprint

PROJECT = "eventhub-dev"


def container(service):
    return PROJECT + "-" + service + "-1"


def sql(statement):
    return (
        admin.run(
            [
                "docker",
                "exec",
                container("postgres"),
                "psql",
                "-XAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "eventhub",
                "-d",
                "eventhub",
                "-c",
                statement,
            ]
        )
        .decode()
        .strip()
    )


def snapshot(output, keep_stopped=False):
    os.umask(0o077)
    names = [container(s) for s in (*admin.WRITERS, "postgres", "redis")]
    current = json.loads(admin.run(["docker", "inspect", *names]))
    for item in current:
        if item["Config"]["Labels"].get("com.docker.compose.project") != PROJECT:
            raise admin.Failure("invalid")
        if not item["State"]["Running"]:
            raise admin.Failure("invalid")
    if sql("SELECT version_num FROM alembic_version;") != "0011":
        raise admin.Failure("invalid")
    source_api = next(
        item for item in current if item["Name"] == "/" + container("api")
    )
    if source_api["Config"]["Image"] != "eventhub-backend:0.11.0":
        raise admin.Failure("invalid")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    images = {}
    for name in sorted({item["Config"]["Image"] for item in current}):
        image = json.loads(admin.run(["docker", "image", "inspect", name]))[0]
        images[name] = {
            "id": image["Id"],
            "architecture": image["Architecture"],
            "os": image["Os"],
        }
    writers = [container(s) for s in admin.WRITERS]
    finished = False
    try:
        admin.run(["docker", "stop", "--time", "60", *writers])
        if any(
            item["State"]["Running"]
            for item in json.loads(admin.run(["docker", "inspect", *writers]))
        ):
            raise admin.Failure("failed")
        checkpoint = datetime.now(timezone.utc).isoformat()
        before = fingerprint(sql)
        with (output / "database.dump").open("xb") as target:
            admin.run(
                [
                    "docker",
                    "exec",
                    container("postgres"),
                    "pg_dump",
                    "-U",
                    "eventhub",
                    "-d",
                    "eventhub",
                    "-Fc",
                ],
                stdout=target,
            )
        with (output / "blobs.tar.gz").open("xb") as target:
            admin.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--volumes-from",
                    container("api") + ":ro",
                    "--entrypoint",
                    "python",
                    source_api["Image"],
                    "-c",
                    (ROOT / "scripts/release/storage.py").read_text(),
                    "save",
                ],
                stdout=target,
            )
        if fingerprint(sql) != before:
            raise admin.Failure("damaged")
        admin.validate_archive(output / "blobs.tar.gz")
        mounts = {v["Destination"]: v for v in source_api["Mounts"]}
        channel = Path(mounts["/run/secrets/channel_secret"]["Source"])
        if channel.resolve() != (ROOT / ".local/secrets/channel_secret").resolve():
            raise admin.Failure("invalid")
        shutil.copyfile(channel, output / "channel_secret")
        defaults = (ROOT / ".env.example").read_text()
        allowed = {
            line.split("=", 1)[0]
            for line in defaults.splitlines()
            if line.startswith("HUB_")
        }
        live = dict(line.split("=", 1) for line in source_api["Config"]["Env"])
        lines = [
            key + "=" + live[key]
            for key in sorted(allowed & set(live))
            if not any(word in key for word in ("PASSWORD", "SECRET", "TOKEN"))
        ]
        (output / "settings.env").write_text("\n".join(lines) + "\n")
        admin.seal(
            output,
            {
                "format": 1,
                "kind": "backup",
                "release": "0.11.0-offline.1",
                "schema": "0011",
                "images": images,
                "checkpoint": checkpoint,
                "data_fingerprint": before,
            },
            admin.BACKUP_FILES,
        )
        finished = True
        print(
            "Согласованная копия 0.11.0 создана; проверено таблиц:",
            len(before),
            flush=True,
        )
    finally:
        if not keep_stopped or not finished:
            admin.run(["docker", "start", *writers])
            for _ in range(90):
                rows = json.loads(admin.run(["docker", "inspect", *writers]))
                if all(
                    row["State"].get("Health", {}).get("Status") == "healthy"
                    for row in rows
                ):
                    break
                time.sleep(2)
            else:
                raise admin.Failure("resumeFailed")
            print("Исходный стенд возобновлён.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-stopped", action="store_true")
    args = parser.parse_args()
    try:
        snapshot(args.output.resolve(), args.keep_stopped)
    except Exception:  # noqa: BLE001 - no child logs or secrets at CLI boundary
        admin.say("failed")
        sys.exit(1)
