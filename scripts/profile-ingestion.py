"""Profile ingestion on a disposable, loopback-only Compose installation."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=int, default=300)
    parser.add_argument(
        "--backend-version", default="0.11.0", choices=["0.10.2", "0.10.3", "0.11.0"]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert 1 <= args.messages <= 2000
    if args.output.exists():
        raise SystemExit("REPORT_ALREADY_EXISTS")
    project = "eventhub-profile-" + uuid4().hex[:8]
    env = {
        **os.environ,
        "HUB_HTTP_PORT": "8082",
        "HUB_SMTP_PORT": "2527",
        "HUB_DOCKER_SUBNET": "172.31.53.0/24",
        "HUB_INGEST_PER_MINUTE": "600000",
        "HUB_INGESTION_THREADS": "8",
        "HUB_DB_POOL_SIZE": "10",
        "HUB_DB_MAX_OVERFLOW": "5",
        "HUB_BACKGROUND_POLL_SECONDS": ".2",
        "HUB_WORK_BATCH_SIZE": "200",
    }
    image = "eventhub-backend:" + args.backend_version
    services = {
        name: {"image": image}
        for name in [
            "api",
            "migrate",
            "storage-init",
            "ingestion",
            "smtp",
            "scheduler",
            "dispatcher",
            "delivery",
            "worker",
        ]
    }
    services["api"].update(
        {
            "command": [
                "python",
                "-m",
                "uvicorn",
                "ingest_profile_app:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--no-access-log",
            ],
            "environment": {"PYTHONPATH": "/app:/opt/eventhub-profile"},
            "volumes": [
                str(ROOT / "scripts/ingest_profile_app.py")
                + ":/opt/eventhub-profile/ingest_profile_app.py:ro"
            ],
        }
    )
    with tempfile.TemporaryDirectory(
        prefix="profile-", dir=ROOT / ".local"
    ) as directory:
        override = Path(directory) / "compose.json"
        override.write_text(json.dumps({"services": services}))
        base = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            "infra/compose/compose.yaml",
            "-f",
            str(override),
        ]

        def run(*commands, input_text=None):
            result = subprocess.run(
                [*base, *commands],
                cwd=ROOT,
                env=env,
                input=input_text,
                text=True,
                capture_output=True,
            )
            if result.returncode:
                raise RuntimeError("PROFILE_COMMAND_FAILED:" + commands[0])
            return result.stdout

        def python(code):
            return run("exec", "-T", "api", "python", "-", input_text=code)

        def profile():
            with urllib.request.urlopen(
                "http://127.0.0.1:8082/api/v1/_load-profile", timeout=15
            ) as response:
                return json.load(response)

        report = {
            "backend_image": image,
            "backend_image_id": subprocess.run(
                ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "instrumentation_sha256": hashlib.sha256(
                (ROOT / "scripts/ingest_profile_app.py").read_bytes()
            ).hexdigest(),
            "messages_per_trial": args.messages,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "profiling": "nested spans overlap; first 20000 samples per label; test-only API",
            "trials": [],
        }
        try:
            print("Запуск отдельного стенда профилирования…", flush=True)
            run("up", "-d", "--no-build", "--wait")
            seed = json.loads(
                python("""
import json
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.services.ingestion import issue_key
engine = build_engine(Settings())
with build_session_factory(engine).begin() as db:
    keys = [issue_key(db, 'Измерение ' + str(i))[1] for i in range(8)]
print(json.dumps(keys))
engine.dispose()
""")
            )
            receipts = set()
            for concurrency, keys, normalize in [
                (1, 1, True),
                (8, 1, True),
                (8, 8, True),
                (8, 8, False),
            ]:
                if not normalize:
                    run("stop", "ingestion")
                profile()

                def send(index, keys=keys):
                    body = json.dumps(
                        {
                            "subject": "Профилирование "
                            + str(len(report["trials"]))
                            + "-"
                            + str(index),
                            "body": "x" * 256,
                        }
                    ).encode()
                    request = urllib.request.Request(
                        "http://127.0.0.1:8082/api/v1/ingest",
                        data=body,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + seed[index % keys],
                        },
                    )
                    start = time.perf_counter()
                    with urllib.request.urlopen(request, timeout=60) as response:
                        assert response.status == 202
                        identifier = json.load(response)["receipt_id"]
                    return identifier, (time.perf_counter() - start) * 1000

                start = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(concurrency) as pool:
                    results = list(pool.map(send, range(args.messages)))
                elapsed = time.perf_counter() - start
                timings = sorted(item[1] for item in results)
                ids = {item[0] for item in results}
                assert len(ids) == args.messages and not (receipts & ids)
                receipts.update(ids)
                trial = {
                    "concurrency": concurrency,
                    "keys": keys,
                    "normalization_running": normalize,
                    "accepted_per_second": round(args.messages / elapsed, 2),
                    "elapsed_seconds": round(elapsed, 3),
                    "http_p50_ms": round(timings[len(timings) // 2], 2),
                    "http_p95_ms": round(timings[int((len(timings) - 1) * 0.95)], 2),
                    "spans": profile(),
                }
                report["trials"].append(trial)
                print(
                    f"Ключей: {keys}; параллельных запросов: {concurrency}; "
                    f"нормализация: {normalize}; приём: {trial['accepted_per_second']}/с",
                    flush=True,
                )
                if not normalize:
                    run("start", "ingestion")
                expected = len(receipts)
                deadline = time.monotonic() + 120
                while True:
                    counts = json.loads(
                        python("""
import json
from sqlalchemy import select, func
from app.settings import Settings
from app.persistence.database import build_engine
from app.persistence.models import RawMessage, EventOccurrence, WorkItem
engine = build_engine(Settings())
with engine.connect() as db:
    print(json.dumps({'raw': db.scalar(select(func.count()).select_from(RawMessage)),
                      'occurrences': db.scalar(select(func.count()).select_from(EventOccurrence)),
                      'failed': db.scalar(select(func.count()).select_from(WorkItem)
                                         .where(WorkItem.state == 'FAILED'))}))
engine.dispose()
""")
                    )
                    assert counts["failed"] == 0 and counts["raw"] == expected
                    if counts["occurrences"] == expected:
                        break
                    assert time.monotonic() < deadline, "NORMALIZATION_DID_NOT_DRAIN"
                    time.sleep(0.5)
                trial["verified_counts"] = counts
            report["verified_files"] = int(
                python("""
from sqlalchemy import select
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import RawMessage, BlobRecord
from app.services.ingestion import store_for
settings = Settings(); engine = build_engine(settings); store = store_for(settings)
with build_session_factory(engine)() as db:
    rows = db.execute(select(RawMessage, BlobRecord)
                      .join(BlobRecord, RawMessage.blob_id == BlobRecord.id)).all()
    for raw, blob in rows:
        assert blob.state == 'READY'
        store.read(blob.id, blob.size, blob.checksum)
    print(len(rows))
engine.dispose()
""").strip()
            )
            assert report["verified_files"] == len(receipts)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(
                "Измерения сохранены. Все подтверждённые сообщения и исходные файлы проверены.",
                flush=True,
            )
        finally:
            run("down", "--volumes", "--remove-orphans")
            print("Стенд профилирования удалён.", flush=True)


if __name__ == "__main__":
    main()
