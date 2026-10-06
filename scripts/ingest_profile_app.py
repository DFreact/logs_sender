"""Test-only ASGI entrypoint, mounted ONLY by profile-ingestion.py.

Never packaged in the backend image. Captures timings, never values/parameters.
Nested spans overlap and must not be added together. Samples are bounded.
"""

import re
import threading
import time
from collections import defaultdict
from functools import wraps

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.api import ingest
from app.main import create_app
from app.persistence.database import build_engine, build_session_factory
from app.services import ingestion
from app.settings import Settings
from app.storage.files import BlobStore

LOCK = threading.Lock()
SPANS = defaultdict(list)


def observe(name, start):
    elapsed = (time.perf_counter() - start) * 1000
    with LOCK:
        if len(SPANS[name]) < 20000:
            SPANS[name].append(elapsed)


def timed(name, function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        label = name
        if name == "credential":
            label += (
                ".consume"
                if kwargs.get("consume")
                else ".exclusive"
                if kwargs.get("exclusive")
                else ".read"
            )
        start = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            observe(label, start)

    return wrapper


settings = Settings()
engine = build_engine(settings)
factory = build_session_factory(engine)


@event.listens_for(engine, "before_cursor_execute")
def before_sql(connection, cursor, statement, parameters, context, executemany):
    context.load_profile_start = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def after_sql(connection, cursor, statement, parameters, context, executemany):
    sql = statement.lower()
    table = re.search(r"(?:from|update|into)\s+([a-z_]+)", sql)
    label = "sql." + (
        "clock"
        if "clock_timestamp()" in sql
        else statement.split()[0].lower() + "." + (table.group(1) if table else "other")
    )
    if "for share" in sql:
        label += ".share"
    elif "for update" in sql:
        label += ".update_lock"
    observe(label, context.load_profile_start)


@event.listens_for(Session, "before_commit")
def before_commit(session):
    session.info["load_profile_commit"] = time.perf_counter()


@event.listens_for(Session, "after_commit")
def after_commit(session):
    start = session.info.pop("load_profile_commit", None)
    if start is not None:
        observe("transaction.flush_and_commit", start)


ingestion.credential = timed("credential", ingestion.credential)
ingest.credential = ingestion.credential
ingestion.adapter = timed("adapter", ingestion.adapter)
if hasattr(ingestion, "stage"):
    ingestion.stage = timed("blob.stage", ingestion.stage)
BlobStore.write = timed("blob.write", BlobStore.write)
BlobStore._sync = staticmethod(timed("blob.directory_fsync", BlobStore._sync))
ingest.receive = timed("receive", ingest.receive)
ingest.authenticate = timed("authenticate", ingest.authenticate)
app = create_app(settings, factory)
app.state.engine = engine


@app.get("/api/v1/_load-profile")
def metrics():
    with LOCK:
        result = {}
        for name, samples in SPANS.items():
            values = sorted(samples)
            result[name] = {
                "samples": len(values),
                "sum_ms": round(sum(values), 3),
                "p50_ms": round(values[len(values) // 2], 3),
                "p95_ms": round(values[int((len(values) - 1) * 0.95)], 3),
                "max_ms": round(values[-1], 3),
            }
        SPANS.clear()
    return result
