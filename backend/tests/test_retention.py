from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_identity import ORIGIN, create_user, headers, login
from test_ingestion import ingest as ingest
from test_ingestion import mail, process

from app.observability.metrics import counters, current, sample
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    Attachment,
    BlobRecord,
    Event,
    EventOccurrence,
    RawMessage,
    RetentionPolicy,
    Source,
    WorkItem,
)
from app.services.ingestion import receive
from app.services.retention import DEFAULTS, recalculate
from app.services.retention.cleanup import raw_files, retire_events, run_batch
from app.workers.execution import execute
from app.workers.queue import enqueue, utc


def delivered_input(ingest):
    factory, store, settings, _, token = ingest
    receive(factory, store, mail(), "message/rfc822", kind="SMTP")
    process(ingest)
    with factory() as db:
        raw = db.scalar(select(RawMessage))
        event = db.scalar(select(Event))
        return raw.id, event.id, utc(raw.received_at)


def policy(factory, **days):
    with factory.begin() as db:
        db.add(
            RetentionPolicy(
                team_id=DEFAULT_TEAM_ID,
                scope="GLOBAL",
                enabled=True,
                inherit=False,
                configuration={**DEFAULTS, **days},
            )
        )


def test_raw_boundary_attachment_budget_and_durable_delete(ingest):
    factory, store, settings, _, _ = ingest
    raw_id, event_id, now = delivered_input(ingest)
    with factory.begin() as db:
        assert (
            raw_files(db, DEFAULT_TEAM_ID, now + timedelta(days=30) - timedelta(microseconds=1), 1)
            == 0
        )
        assert raw_files(db, DEFAULT_TEAM_ID, now + timedelta(days=30), 1) == 1
    with factory() as db:
        assert db.get(RawMessage, raw_id).blob_id is not None  # Attachment consumed the batch.
        assert db.scalar(select(Attachment)) is None
    with factory.begin() as db:
        raw_files(db, DEFAULT_TEAM_ID, now + timedelta(days=30), 1)
    with factory() as db:
        assert db.get(RawMessage, raw_id).purged_at is not None
        assert db.get(Event, event_id) is not None
        tasks = db.scalars(select(WorkItem.id).where(WorkItem.kind == "BLOB_DELETE")).all()
        blobs = db.scalars(select(BlobRecord)).all()
        assert len(tasks) == 2 and all(row.state == "DELETING" for row in blobs)
    for task in tasks:
        execute(factory, store, settings, str(task))
    with factory() as db:
        assert all(row.state == "DELETED" for row in db.scalars(select(BlobRecord)))


def test_disabled_default_then_bounded_recalculation_and_full_event_cleanup(ingest):
    factory, _, settings, _, _ = ingest
    raw_id, event_id, now = delivered_input(ingest)
    later = now + timedelta(days=100)
    assert run_batch(factory, settings, DEFAULT_TEAM_ID, now=later) == 0
    policy(factory)
    settings.work_batch_size = 1
    run_batch(factory, settings, DEFAULT_TEAM_ID, now=later)
    with factory() as db:
        assert db.get(RawMessage, raw_id).purged_at is None  # Recalculation not complete.
    for _ in range(25):
        run_batch(factory, settings, DEFAULT_TEAM_ID, now=later)
    with factory() as db:
        assert db.get(Event, event_id) is None
        assert db.get(RawMessage, raw_id) is None
        assert db.scalar(select(EventOccurrence)) is None
        values = counters(db)
        assert values["events"] == 0 and values["accepted_total"] == 1
        assert values["normalized_total"] == 1


def test_extending_policy_prevents_old_deadline_cleanup(ingest):
    factory, _, settings, _, _ = ingest
    raw_id, event_id, now = delivered_input(ingest)
    policy(factory, event_days=200, raw_days=150)
    settings.work_batch_size = 1
    for _ in range(5):
        run_batch(factory, settings, DEFAULT_TEAM_ID, now=now + timedelta(days=100))
    with factory() as db:
        assert db.get(RawMessage, raw_id).purged_at is None
        assert utc(db.get(Event, event_id).expires_at) == now + timedelta(days=200)


def test_source_override_backfills_old_raw_metadata(ingest):
    factory, _, _, _, _ = ingest
    raw_id, event_id, now = delivered_input(ingest)
    with factory.begin() as db:
        source = Source(team_id=DEFAULT_TEAM_ID, name="source")
        db.add(source)
        db.flush()
        db.get(Event, event_id).source_id = source.id
        db.add(
            RetentionPolicy(
                team_id=DEFAULT_TEAM_ID,
                scope=str(source.id),
                inherit=False,
                configuration={"event_days": 5, "raw_days": 2},
            )
        )
        db.flush()
        recalculate(db, DEFAULT_TEAM_ID, 1, now, 100)
        assert db.get(RawMessage, raw_id).source_id == source.id
        assert utc(db.get(RawMessage, raw_id).expires_at) == now + timedelta(days=2)
        assert utc(db.get(Event, event_id).expires_at) == now + timedelta(days=5)


def test_active_normalization_holds_original_and_event(ingest):
    factory, _, _, _, _ = ingest
    raw_id, event_id, now = delivered_input(ingest)
    with factory.begin() as db:
        enqueue(db, "NORMALIZE", raw_id, uuid4().hex)
        assert raw_files(db, DEFAULT_TEAM_ID, now + timedelta(days=100), 20) == 0
        assert retire_events(db, DEFAULT_TEAM_ID, now + timedelta(days=100), 20) == 0
        assert not db.get(Event, event_id).retiring


def test_counter_changes_rollback_and_health_unknown_rate(ingest):
    factory, _, settings, _, _ = ingest
    _, event_id, now = delivered_input(ingest)
    with pytest.raises(RuntimeError):
        with factory.begin() as db:
            event = db.get(Event, event_id)
            event.status = "RESOLVED"
            db.flush()
            assert counters(db)["events_new"] == 0
            raise RuntimeError("rollback")
    with factory() as db:
        assert counters(db)["events_new"] == 1
        assert current(db)["rates"]["accepted_total"] is None
    sample(factory, settings, now=now - timedelta(minutes=1))
    sample(factory, settings, now=now)
    with factory() as db:
        assert current(db)["rates"]["accepted_total"] == 0


@pytest.mark.anyio
async def test_retention_settings_validation_csrf_conflicts_and_roles(setup):
    app, factory, password, _, _ = setup
    path = "/api/v1/settings/retention"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (await client.get(path)).status_code == 401
        auth = headers(await login(client, password))
        result = (await client.get(path)).json()
        assert not result["enabled"] and result["version"] == 0
        body = {key: result[key] for key in ("enabled", "version", "inherit", "days")}
        body["enabled"] = True
        assert (await client.patch(path, json=body)).status_code == 403
        assert (await client.patch(path, json=body, headers=auth)).status_code == 200
        assert (await client.patch(path, json=body, headers=auth)).status_code == 409
        for invalid in (0, 3651, True, "3", 1.5):
            bad = {**body, "version": 1, "days": {**DEFAULTS, "event_days": invalid}}
            assert (await client.patch(path, json=bad, headers=auth)).status_code == 422
        bad = {**body, "version": 1, "days": {**DEFAULTS, "raw_days": 100}}
        assert (await client.patch(path, json=bad, headers=auth)).status_code == 422
        user, secret = await create_user(client, auth, username="reader", role="VIEWER")
        viewer = headers(await login(client, secret, user["username"]))
        assert (await client.get(path)).status_code == 403
        assert (await client.patch(path, json=body, headers=viewer)).status_code == 403


@pytest.mark.anyio
async def test_expired_raw_is_explained_in_api(setup, ingest):
    raw_id, event_id, now = delivered_input(ingest)
    with ingest[0].begin() as db:
        raw_files(db, DEFAULT_TEAM_ID, now + timedelta(days=30), 20)
    async with AsyncClient(transport=ASGITransport(app=setup[0]), base_url=ORIGIN) as client:
        await login(client, setup[2])
        detail = (await client.get(f"/api/v1/events/{event_id}")).json()
        assert detail["occurrences"][0]["raw_available"] is False
        response = await client.get(f"/api/v1/events/{event_id}/raw/{raw_id}")
        assert response.status_code == 410 and response.json()["code"] == "RAW_MESSAGE_EXPIRED"


def test_active_delivery_holds_event_then_history_can_expire(ingest, background):
    from test_notifications import pending, seed

    from app.persistence.models import Notification

    factory, _, settings, actor = background
    raw_id, event_id, now = delivered_input(ingest)
    channel = seed(factory, actor, settings)
    note_id, work_id = pending(factory, channel, settings, event_id=event_id)
    later = now + timedelta(days=100)
    with factory.begin() as db:
        raw_files(db, DEFAULT_TEAM_ID, later, 100)
        assert retire_events(db, DEFAULT_TEAM_ID, later, 100) == 0
        assert not db.get(Event, event_id).retiring
        note = db.get(Notification, note_id)
        note.status, note.finished_at = "SENT", now
        work = db.get(WorkItem, work_id)
        work.state, work.completed_at = "SUCCEEDED", now
    policy(factory)
    for _ in range(20):
        run_batch(factory, settings, DEFAULT_TEAM_ID, now=later)
    with factory() as db:
        assert db.get(Event, event_id) is None
        assert db.get(Notification, note_id) is None
        assert counters(db)["sent_total"] == 1
        assert counters(db)["notification_SENT"] == 0


def test_digest_snapshot_holds_events_until_history_expires(background):
    from test_digests import END, START, definition
    from test_digests import ingest as digest_ingest

    from app.persistence.models import DigestItem, DigestRun, Notification
    from app.services.digests import advance
    from app.services.retention.cleanup import digest_history

    factory, _, settings, _ = background
    definition(background)
    digest_ingest(background, START)
    advance(factory, settings, now=END)
    with factory.begin() as db:
        run = db.scalar(select(DigestRun))
        event_id = db.scalar(select(Event.id))
        assert db.scalar(select(DigestItem)) is not None
        later = END + timedelta(days=100)
        raw_files(db, DEFAULT_TEAM_ID, later, 100)
        assert retire_events(db, DEFAULT_TEAM_ID, later, 100) == 0
        assert digest_history(db, DEFAULT_TEAM_ID, later, 90, 1) == 0
        note = db.get(Notification, run.notification_id)
        note.status, note.finished_at = "SENT", END
        db.flush()
        assert digest_history(db, DEFAULT_TEAM_ID, later, 90, 1) == 2
        assert db.scalar(select(DigestItem)) is None
        assert db.scalar(select(DigestRun)) is None
    for _ in range(10):
        with factory.begin() as db:
            retire_events(db, DEFAULT_TEAM_ID, later, 2)
    with factory() as db:
        assert db.get(Event, event_id) is None


def test_populated_retention_migration_seeds_counters(ingest):
    from alembic import command
    from alembic.config import Config

    factory, _, _, _, _ = ingest
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL populated migration")
    _, event_id, _ = delivered_input(ingest)
    config = Config("alembic.ini")
    with factory.kw["bind"].begin() as connection:
        config.attributes["connection"] = connection
        command.stamp(config, "0012")
        command.downgrade(config, "0010b")
        command.upgrade(config, "0012")
        command.check(config)
    with factory() as db:
        assert db.get(Event, event_id) is not None
        assert counters(db)["events"] == 1
        assert counters(db)["accepted_total"] == 1
        assert counters(db)["normalized_total"] == 1


@pytest.mark.anyio
async def test_metrics_endpoint_permissions_and_safe_names(setup):
    app, _, password, _, _ = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (await client.get("/metrics")).status_code == 401
        auth = headers(await login(client, password))
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "eventhub_sample_fresh 0" in response.text
        assert all(line.startswith("eventhub_") for line in response.text.splitlines())
        user, secret = await create_user(client, auth, username="metrics-reader", role="VIEWER")
        await login(client, secret, user["username"])
        assert (await client.get("/metrics")).status_code == 403
        assert (await client.get("/api/v1/overview")).status_code == 200


def test_linux_resources_unknown_and_counter_reset(tmp_path):
    from app.observability.resources import cpu_percent, read_resources

    assert read_resources(tmp_path) == {"cpu": None, "memory": None}
    assert (
        cpu_percent(
            {"boot": "new", "total": 100, "busy": 20}, {"boot": "old", "total": 80, "busy": 10}
        )
        is None
    )
    assert (
        cpu_percent(
            {"boot": "same", "total": 100, "busy": 20}, {"boot": "same", "total": 80, "busy": 10}
        )
        == 50
    )


def test_postgres_large_archive_cleanup_uses_deadline_index_and_keeps_ingest(ingest):
    from concurrent.futures import ThreadPoolExecutor
    from time import perf_counter

    from sqlalchemy import insert, text

    from app.security.tokens import digest

    factory, store, settings, _, token = ingest
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL execution plans and concurrency")
    _, old_event, now = delivered_input(ingest)
    # A cold archive larger than one cleanup batch. Future deadlines must be skipped by index.
    with factory.begin() as db:
        db.execute(
            insert(Event),
            [
                dict(
                    id=uuid4(),
                    team_id=DEFAULT_TEAM_ID,
                    input_adapter="REST",
                    subject="archive",
                    received_at=now,
                    first_seen_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(days=365),
                )
                for _ in range(10000)
            ],
        )
        db.execute(text("ANALYZE events"))
        plan = db.scalar(
            text(
                "EXPLAIN (ANALYZE, FORMAT JSON) SELECT id FROM events "
                "WHERE team_id = :team AND expires_at <= :deadline "
                "ORDER BY expires_at, id LIMIT 1 FOR UPDATE"
            ),
            {"team": DEFAULT_TEAM_ID, "deadline": now + timedelta(days=100)},
        )
        assert "ix_event_expiry" in str(plan)

    def clean():
        for _ in range(6):
            with factory.begin() as db:
                db.execute(text("SET LOCAL lock_timeout = '1s'"))
                db.execute(text("SET LOCAL statement_timeout = '3s'"))
                raw_files(db, DEFAULT_TEAM_ID, now + timedelta(days=100), 50)
                retire_events(db, DEFAULT_TEAM_ID, now + timedelta(days=100), 50)

    def receive_more():
        started = perf_counter()
        for _ in range(12):
            receive(
                factory, store, b"new input", "text/plain", kind="REST", token_hash=digest(token)
            )
        return perf_counter() - started

    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanup = pool.submit(clean)
        elapsed = pool.submit(receive_more).result(timeout=30)
        cleanup.result(timeout=30)
    with factory() as db:
        assert db.get(Event, old_event) is None
        assert counters(db)["events"] == 10000
        assert counters(db)["accepted_total"] == 13
    print(f"retention archive=10000, accepted=12, concurrent_seconds={elapsed:.3f}")


def test_cleanup_scans_past_held_events_and_stops_after_full_pass(ingest, background):
    from test_notifications import pending, seed

    from app.persistence.models import RetentionProgress

    factory, _, settings, actor = background
    _, event_id, now = delivered_input(ingest)
    with factory.begin() as db:
        held_event = Event(
            team_id=DEFAULT_TEAM_ID,
            input_adapter="REST",
            received_at=now - timedelta(days=1),
            first_seen_at=now - timedelta(days=1),
            last_seen_at=now - timedelta(days=1),
        )
        db.add(held_event)
        db.flush()
        held_id = held_event.id
    pending(factory, seed(factory, actor, settings), settings, event_id=held_id)
    policy(factory)
    for _ in range(20):
        changed = run_batch(factory, settings, DEFAULT_TEAM_ID, now=now + timedelta(days=100))
        if not changed:
            break
    else:
        pytest.fail("An entirely held archive must not loop continuously")
    with factory() as db:
        assert db.get(Event, held_id) is not None
        assert db.get(Event, event_id) is None
        assert db.get(RetentionProgress, (DEFAULT_TEAM_ID, "EVENTS")).cursor is None


def test_health_does_not_count_or_load_archive_rows(ingest):
    from sqlalchemy import event as sql_event
    from test_health import healthy

    from app.observability.health import snapshot

    factory, _, settings, _, _ = ingest
    delivered_input(ingest)
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    engine = factory.kw["bind"]
    sql_event.listen(engine, "before_cursor_execute", capture)
    try:
        with factory() as db:
            result = snapshot(db, settings, probe=healthy)
            assert result["queue"]["SUCCEEDED"] == 1
            current(db)
    finally:
        sql_event.remove(engine, "before_cursor_execute", capture)
    assert not any("from events" in sql or "from raw_messages" in sql for sql in statements)
    assert all(
        "count(" not in sql and "limit" in sql for sql in statements if "from work_items" in sql
    )
