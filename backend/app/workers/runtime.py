"""Separate worker, scheduler and dispatcher processes with safe diagnostic logs."""

import json
import logging
import signal
import sys
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

import dramatiq

from app.observability.health import heartbeat
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import WorkItem
from app.scheduler.service import schedule_once
from app.settings import Settings
from app.storage.files import BlobStore
from app.workers.dispatcher import dispatch_once
from app.workers.execution import execute
from app.workers.transport import actor_for, broker_for


class SafeLogs(logging.Handler):
    def emit(self, record):
        # Third-party log records may interpolate message args or credentials.
        # Preserve the safe first-party stack payload, otherwise only location/level.
        if record.name == "eventhub.errors":
            sys.stderr.write(record.getMessage() + "\n")
        else:
            sys.stderr.write(
                json.dumps(
                    {
                        "logger": record.name,
                        "level": record.levelname,
                        "file": record.filename,
                        "line": record.lineno,
                    }
                )
                + "\n"
            )


def main():
    logging.basicConfig(level=logging.WARNING, handlers=[SafeLogs()], force=True)
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in {"worker", "ingestion", "delivery", "scheduler", "dispatcher"}:
        raise SystemExit(2)
    settings = Settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    store = BlobStore(
        settings.storage_root, settings.blob_max_bytes, settings.storage_min_free_bytes
    )
    stopped, instance_id = Event(), uuid4()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    broker = worker = None
    try:
        if mode != "scheduler":
            broker = broker_for(settings)
            actor = actor_for(
                broker, lambda identifier: execute(factory, store, settings, identifier)
            )
            ingestion_actor = actor_for(
                broker,
                lambda identifier: execute(factory, store, settings, identifier),
                "ingestion",
            )
            delivery_actor = actor_for(
                broker, lambda identifier: execute(factory, store, settings, identifier), "delivery"
            )
        if mode in {"worker", "ingestion", "delivery"}:
            worker = dramatiq.Worker(
                broker,
                queues={mode if mode in {"ingestion", "delivery"} else "maintenance"},
                worker_threads={
                    "ingestion": settings.ingestion_threads,
                    "delivery": settings.delivery_threads,
                    "worker": settings.maintenance_threads,
                }[mode],
            )
            worker.start()
        while not stopped.is_set():
            status, code = "HEALTHY", "OK"
            published = 0
            try:
                if mode == "scheduler":
                    schedule_once(factory, settings)
                else:
                    broker.client.ping()
                    if mode == "dispatcher":

                        def publish(identifier):
                            from uuid import UUID

                            with factory() as db:
                                item = db.get(WorkItem, UUID(identifier))
                                target = (
                                    ingestion_actor
                                    if item and item.kind == "NORMALIZE"
                                    else delivery_actor
                                    if item and item.kind == "DELIVER"
                                    else actor
                                )
                            target.send(identifier)

                        published = dispatch_once(factory, settings, publish)
            except Exception:
                status, code = "UNAVAILABLE", "UNREACHABLE"
            try:
                heartbeat(factory, mode.upper(), instance_id, status, code)
                Path("/tmp/eventhub-background-alive").write_text(str(time.time()))
            except Exception:
                logging.getLogger("eventhub.background").warning("HEARTBEAT_FAILED")
            # Drain full batches immediately; only an idle/partial pass waits.
            stopped.wait(
                0 if published >= settings.work_batch_size else settings.background_poll_seconds
            )
    finally:
        if worker:
            worker.stop(timeout=10000)
        if broker:
            broker.close()
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Startup failures must not dump environment values or connection strings.
        sys.stderr.write('{"code":"BACKGROUND_START_FAILED"}\n')
        raise SystemExit(1) from None
