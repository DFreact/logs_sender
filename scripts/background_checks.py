"""Failure drills for the disposable verification stack only."""

import json
import time
import urllib.error
import urllib.request

COMMON = """
import json
import os
from uuid import UUID, uuid4
from datetime import timedelta
from sqlalchemy import select
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import BlobRecord, WorkItem
from app.storage.files import BlobStore
from app.storage.service import stage, request_delete
from app.workers.queue import claim, clock
from app.workers.transport import broker_for, actor_for
from app.workers.dispatcher import dispatch_once
settings = Settings()
engine = build_engine(settings)
factory = build_session_factory(engine)
store = BlobStore(settings.storage_root, settings.blob_max_bytes, settings.storage_min_free_bytes)
"""


def verify(run, base):
    def python(body, **params):
        result = run(
            "exec",
            "-T",
            "api",
            "python",
            "-",
            input_text=COMMON + "\nparams = " + repr(params) + "\n" + body,
        )
        return json.loads(result) if result.strip() else None

    def wait_until(check, timeout=50):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(1)
        raise AssertionError("BACKGROUND_RECOVERY_TIMEOUT")

    def readiness():
        try:
            response = urllib.request.urlopen(base + "/health/ready", timeout=12)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = json.loads(response.read())
            assert set(data) <= {
                "status",
                "code",
                "params",
                "field_errors",
                "request_id",
            }
            return response.status

    def create(delay=0):
        return python(
            """
identifier = stage(factory, store, [b"isolated failure drill"])
with factory.begin() as db:
    record = db.get(BlobRecord, identifier)
    request_delete(db, record)
    work = db.scalar(select(WorkItem).where(WorkItem.entity_id == identifier))
    work.due_at = clock(db) + timedelta(seconds=params["delay"])
    print(json.dumps({"id": str(work.id), "blob": str(identifier)}))
""",
            delay=delay,
        )

    def state(task):
        return python(
            """
with factory() as db:
    work = db.get(WorkItem, UUID(params["id"]))
    record = db.get(BlobRecord, UUID(params["blob"]))
    print(json.dumps({"state": work.state, "attempts": work.attempts,
                      "completed": str(work.completed_at), "blob": record.state,
                      "exists": store._path(record.id).exists()}))
""",
            **task,
        )

    wait_until(lambda: readiness() == 200)
    run("stop", "worker", "dispatcher")
    task = create()
    # Publication succeeds, then the entire volatile broker process is lost.
    python("""
broker = broker_for(settings)
actor = actor_for(broker, lambda _: None)
dispatch_once(factory, settings, actor.send)
broker.close()
""")
    run("restart", "redis")
    run("start", "dispatcher", "worker")
    wait_until(lambda: state(task)["state"] == "SUCCEEDED")
    before = state(task)
    assert before["blob"] == "DELETED" and not before["exists"]
    python(
        """
broker = broker_for(settings)
actor = actor_for(broker, lambda _: None)
actor.send(params["id"])
actor.send(params["id"])
broker.close()
""",
        **task,
    )
    time.sleep(3)
    assert state(task) == before
    print(
        "Потеря очереди после публикации и повторные сообщения: пройдено.", flush=True
    )

    run("stop", "redis")
    try:
        assert readiness() == 503
        offline = create()
        assert state(offline)["state"] == "PENDING"
    finally:
        run("start", "redis")
    wait_until(lambda: state(offline)["state"] == "SUCCEEDED")
    print(
        "Создание задачи при недоступной очереди и восстановление: пройдено.",
        flush=True,
    )

    delayed = create(delay=8)
    time.sleep(2)
    assert state(delayed)["state"] == "PENDING" and state(delayed)["exists"]
    wait_until(lambda: state(delayed)["state"] == "SUCCEEDED")

    run("stop", "worker")
    interrupted = create()
    python(
        """
with factory.begin() as db:
    assert claim(db, UUID(params["id"]), 10) is not None
""",
        **interrupted,
    )
    run("start", "worker")
    wait_until(lambda: state(interrupted)["state"] == "SUCCEEDED")
    assert state(interrupted)["attempts"] == 2
    print(
        "Срок задачи, перезапуск обработчика и восстановление аренды: пройдено.",
        flush=True,
    )

    python("""
for number in range(256):
    (store.root / f"{number:02x}").chmod(0o500)
try:
    try:
        stage(factory, store, [b"must not be acknowledged"])
    except OSError:
        pass
    else:
        raise AssertionError("WRITE_SHOULD_FAIL")
    with factory() as db:
        failed = db.scalars(select(BlobRecord).where(BlobRecord.state == "STAGING")).all()
        assert any(record.checksum is None for record in failed)
finally:
    for number in range(256):
        (store.root / f"{number:02x}").chmod(0o700)
""")
    run("restart", "scheduler", "dispatcher", "worker")
    wait_until(lambda: readiness() == 200)
    print(
        "Отказ записи и восстановление готовности после перезапуска: пройдено.",
        flush=True,
    )
