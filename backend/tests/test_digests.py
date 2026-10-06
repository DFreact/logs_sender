from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_identity import create_user, headers, login
from test_notifications import seed
from test_routing import action, save_rule
from test_sources import configured, receipt

from app.api.digest_schemas import DigestInput
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    DigestDefinition,
    DigestItem,
    DigestReceipt,
    DigestRun,
    Event,
    EventOccurrence,
    Notification,
    NotificationChannel,
    RawMessage,
    WorkItem,
)
from app.services.digests import advance, snapshot
from app.services.digests.schedule import boundary, next_boundary, window_start
from app.workers.execution import execute
from app.workers.queue import utc

START = datetime(2026, 1, 10, 5, tzinfo=UTC)
END = START + timedelta(days=1)


def definition(background, **overrides):
    factory, _, settings, actor = background
    configured(factory, actor)
    channel = seed(factory, actor, settings)
    with factory.begin() as db:
        template = db.get(NotificationChannel, channel).template_id
        config = DigestInput(
            name="События межсетевого экрана", channel_id=channel, template_id=template, **overrides
        ).model_dump(mode="json")
        row = DigestDefinition(
            team_id=DEFAULT_TEAM_ID,
            name=config.pop("name"),
            description=config.pop("description"),
            enabled=config.pop("enabled"),
            version=config.pop("version"),
            configuration=config,
            created_at=START,
            window_start=START,
            next_end=END,
            window_snapshot={},
        )
        row.window_snapshot = snapshot(row)
        db.add(row)
        db.flush()
        return row.id


def ingest(background, at, *, process=True):
    factory, store, settings, _ = background
    raw, work = receipt(factory, store)
    with factory.begin() as db:
        db.get(RawMessage, raw).received_at = at
    if process:
        execute(factory, store, settings, str(work))
        with factory.begin() as db:
            row = db.scalar(
                select(DigestReceipt)
                .join(Event, Event.id == DigestReceipt.event_id)
                .where(
                    DigestReceipt.occurrence_id.in_(
                        select(EventOccurrence.id).where(EventOccurrence.raw_message_id == raw)
                    )
                )
            )
            row.processed_at = at + timedelta(seconds=1)
    return raw, work


def test_calendar_gaps_overlaps_and_absolute_24_hours():
    assert boundary(datetime(2026, 3, 29).date(), 150, "Europe/Berlin") == datetime(
        2026, 3, 29, 1, tzinfo=UTC
    )
    assert boundary(datetime(2026, 10, 25).date(), 150, "Europe/Berlin") == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )
    cfg = {"minute_of_day": 480, "time_zone": "Europe/Berlin", "period": "CALENDAR"}
    before = boundary(datetime(2026, 3, 28).date(), 480, "Europe/Berlin")
    end = next_boundary(before, cfg)
    assert (end - before).total_seconds() == 23 * 3600
    assert window_start(before, end, START, cfg) == before
    cfg["period"] = "LAST_24_HOURS"
    assert (end - window_start(before, end, START, cfg)).total_seconds() == 24 * 3600
    repeated = boundary(datetime(2026, 10, 25).date(), 150, "Europe/Berlin")
    assert next_boundary(repeated, {**cfg, "minute_of_day": 150}).date().isoformat() == "2026-10-26"


@pytest.mark.parametrize(
    "config",
    [
        {"time_zone": "../../etc/passwd"},
        {"minute_of_day": 1440},
        {"wait_seconds": 59},
        {"period": "INVALID"},
    ],
)
def test_invalid_configuration(config):
    with pytest.raises(ValueError):
        DigestInput(name="Проверка", channel_id=uuid4(), template_id=uuid4(), **config)


def test_window_counts_occurrences_not_lifetime_and_replays(background):
    factory, _, settings, _ = background
    definition(background)
    for at in (START - timedelta(seconds=1), START, START + timedelta(seconds=2), END):
        ingest(background, at)
    advance(factory, settings, now=END)
    advance(factory, settings, now=END)
    with factory() as db:
        run = db.scalar(select(DigestRun))
        assert run.summary["occurrences"] == 2
        assert run.summary["events"] == 1
        assert run.state == "READY"
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
        assert db.scalar(select(func.count()).select_from(DigestItem)) == 2
        notification = db.get(Notification, run.notification_id)
        assert notification.mode == "DIGEST"
        assert "Поступлений: 2" in notification.prepared["body"]
        assert "Отдельных событий: 1" in notification.prepared["body"]
        assert notification.event_id is None


def test_pending_normalization_waits_and_later_completes(background):
    factory, store, settings, _ = background
    definition(background, wait_seconds=60)
    _, work = ingest(background, START + timedelta(hours=2), process=False)
    advance(factory, settings, now=END)
    with factory() as db:
        assert db.scalar(select(DigestRun)).state == "WAITING"
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
    advance(factory, settings, now=END + timedelta(minutes=2))
    with factory() as db:
        assert db.scalar(select(DigestRun)).state == "ATTENTION"
    execute(factory, store, settings, str(work))
    # Controlled clock moves beyond actual completion; no silently lost late result.
    with factory.begin() as db:
        db.scalar(select(DigestReceipt)).processed_at = END + timedelta(minutes=3)
    advance(factory, settings, now=END + timedelta(minutes=4))
    with factory() as db:
        assert db.scalar(select(DigestRun)).summary["occurrences"] == 1


def test_backlog_windows_are_bounded_and_empty_periods_not_sent(background):
    factory, _, settings, _ = background
    definition(background)
    advance(factory, settings, now=END + timedelta(days=10))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DigestRun)) == 8
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
    advance(factory, settings, now=END + timedelta(days=10))
    with factory() as db:
        rows = db.scalars(select(DigestRun).order_by(DigestRun.window_end)).all()
        assert len(rows) == 11 and all(r.state == "EMPTY" for r in rows)
        assert utc(rows[0].window_start) == START and utc(rows[-1].window_end) == END + timedelta(
            days=10
        )


def test_edit_uses_frozen_current_window_and_disabled_future_windows(background):
    factory, _, settings, _ = background
    identifier = definition(background)
    ingest(background, START + timedelta(hours=2))
    with factory.begin() as db:
        row = db.get(DigestDefinition, identifier)
        row.name, row.enabled, row.version = "Другая версия", False, 2
    advance(factory, settings, now=END + timedelta(days=1))
    with factory() as db:
        rows = db.scalars(select(DigestRun).order_by(DigestRun.window_end)).all()
        assert rows[0].snapshot["version"] == 1 and rows[0].state == "READY"
        assert rows[1].state == "SKIPPED"


def test_rule_selection_and_suppression(background):
    factory, _, settings, actor = background
    identifier = definition(background, selection="RULE")
    save_rule(
        factory, actor, actions=[action("DIGEST", target_id=str(identifier), scope="OCCURRENCE")]
    )
    ingest(background, START + timedelta(seconds=1))
    ingest(background, START + timedelta(seconds=2))
    advance(factory, settings, now=END)
    with factory() as db:
        assert db.scalar(select(DigestRun)).summary["occurrences"] == 2


def test_build_batches_resume_and_suppression_before_finalize(background, monkeypatch):
    import app.services.digests as service

    factory, _, settings, _ = background
    definition(background)
    ingest(background, START + timedelta(seconds=1))
    ingest(background, START + timedelta(seconds=2))
    monkeypatch.setattr(service, "BATCH", 1)
    advance(factory, settings, now=END)
    with factory() as db:
        assert db.scalar(select(DigestRun)).state == "BUILDING"
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
    advance(factory, settings, now=END)
    with factory.begin() as db:
        db.scalar(select(Event)).status = "SUPPRESSED"
    advance(factory, settings, now=END)
    with factory() as db:
        assert db.scalar(select(DigestRun)).state == "EMPTY"
        assert db.scalar(select(func.count()).select_from(DigestItem)) == 0


@pytest.mark.anyio
async def test_partial_decision_idempotency_and_late_history(background, setup):
    app, factory, password, _, settings = setup
    _, store, _, _ = background
    definition(background, wait_seconds=60)
    _, work = ingest(background, START + timedelta(hours=2), process=False)
    advance(factory, settings, now=END + timedelta(minutes=2))
    with factory() as db:
        run = db.scalar(select(DigestRun))
        rid = run.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        identity = await login(client, password)
        op = {"version": 1, "operation_key": str(uuid4())}
        url = f"/api/v1/digests/runs/{rid}/partial"
        assert (await client.post(url, json=op, headers=headers(identity))).status_code == 200
        assert (await client.post(url, json=op, headers=headers(identity))).status_code == 200
        advance(factory, settings)
        execute(factory, store, settings, str(work))
        rows = (await client.get(f"/api/v1/digests/runs/{rid}/items?late=true")).json()
        assert rows["total"] == 1
        with factory() as db:
            assert db.get(DigestRun, rid).state == "EMPTY"
            assert db.get(DigestRun, rid).partial


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["VIEWER", "OPERATOR"])
async def test_permissions_preview_versions_and_safe_history(background, setup, role):
    app, factory, password, _, _ = setup
    identifier = definition(background)
    # API updates require a future current window, to avoid retroactive backlog edits.
    with factory.begin() as db:
        row = db.get(DigestDefinition, identifier)
        row.next_end = datetime.now(UTC) + timedelta(days=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        identity = await login(client, password)
        listing = (await client.get("/api/v1/digests")).json()["items"][0]
        body = {
            k: v
            for k, v in listing.items()
            if k not in ("id", "created_at", "window_start", "next_end")
        }
        body["name"] = "Новая версия"
        response = await client.patch(
            f"/api/v1/digests/{identifier}", json=body, headers=headers(identity)
        )
        assert response.status_code == 200
        assert response.json()["version"] == 2
        versions = (await client.get(f"/api/v1/digests/{identifier}/versions")).json()
        assert versions["items"][0]["author"] == "Администратор"
        before = None
        with factory() as db:
            before = db.scalar(select(func.count()).select_from(WorkItem))
        assert (await client.get(f"/api/v1/digests/{identifier}/preview")).status_code == 200
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(WorkItem)) == before
        _, reader_password = await create_user(client, headers(identity), "reader", role)
        me = await login(client, reader_password, "reader")
        assert (await client.get("/api/v1/digests")).status_code == 200
        denied = await client.patch(f"/api/v1/digests/{identifier}", json=body, headers=headers(me))
        assert denied.status_code == 403


def test_competing_schedulers_create_single_digest(background):
    factory, _, settings, _ = background
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks")
    definition(background)
    ingest(background, START + timedelta(seconds=1))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: advance(factory, settings, now=END), range(16)))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DigestRun)) == 1
        assert db.scalar(select(func.count()).select_from(Notification)) == 1


def test_composition_rollback_retries_without_duplicate_notification(background, monkeypatch):
    import app.services.digests as service

    factory, _, settings, _ = background
    definition(background)
    ingest(background, START + timedelta(seconds=1))
    original = service.create

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("SIMULATED_COMMIT_FAILURE")

    monkeypatch.setattr(service, "create", fail)
    with pytest.raises(RuntimeError, match="SIMULATED_COMMIT_FAILURE"):
        advance(factory, settings, now=END)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
        assert db.scalar(select(func.count()).select_from(DigestItem)) == 0
        assert db.scalar(select(DigestRun)).state == "WAITING"
    monkeypatch.setattr(service, "create", original)
    advance(factory, settings, now=END)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
        assert db.scalar(select(DigestRun)).summary["occurrences"] == 1


def test_filter_uses_occurrence_snapshot_and_empty_send_is_explicit(background):
    factory, _, settings, _ = background
    definition(background, severities=["WARNING"], send_empty=True)
    ingest(background, START + timedelta(seconds=1))
    with factory.begin() as db:
        db.scalar(select(Event)).severity = "CRITICAL"
        db.scalar(select(Event)).occurrence_count = 900
    advance(factory, settings, now=END + timedelta(days=1))
    with factory() as db:
        rows = db.scalars(select(DigestRun).order_by(DigestRun.window_end)).all()
        assert rows[0].summary["occurrences"] == 1
        assert rows[0].summary["severities"] == [{"label": "WARNING", "count": 1}]
        assert rows[1].summary["occurrences"] == 0
        assert len(db.scalars(select(Notification)).all()) == 2


def test_fall_back_calendar_day_and_24_hour_nonoverlap_warning_semantics():
    cfg = {"minute_of_day": 480, "time_zone": "Europe/Berlin", "period": "CALENDAR"}
    start = boundary(datetime(2026, 10, 24).date(), 480, cfg["time_zone"])
    end = next_boundary(start, cfg)
    assert (end - start).total_seconds() == 25 * 3600
    assert window_start(start, end, START, cfg) == start
    assert window_start(start, end, START, {**cfg, "period": "LAST_24_HOURS"}) == start + timedelta(
        hours=1
    )


def test_normalization_commit_cannot_disappear_at_digest_cutoff(background, monkeypatch):
    from threading import Event as Signal

    import app.services.digests as service

    factory, store, settings, _ = background
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL ingestion checkpoint")
    identifier = definition(background)
    now = datetime.now(UTC)
    end = now + timedelta(hours=1)
    with factory.begin() as db:
        row = db.get(DigestDefinition, identifier)
        row.created_at = row.window_start = now - timedelta(hours=1)
        row.next_end = end
    _, task = ingest(background, now, process=False)
    entered, release = Signal(), Signal()
    original = service.capture

    def capture(*args):
        original(*args)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(service, "capture", capture)
    with ThreadPoolExecutor(max_workers=2) as pool:
        normalizer = pool.submit(execute, factory, store, settings, str(task))
        assert entered.wait(10)
        scheduler = pool.submit(advance, factory, settings, now=end)
        release.set()
        normalizer.result(timeout=20)
        scheduler.result(timeout=20)
    with factory() as db:
        run = db.scalar(select(DigestRun))
        assert run.state == "READY" and run.summary["occurrences"] == 1


def test_load_index_migration_restarts_only_unfinished_composition(background, monkeypatch):
    from alembic import command
    from alembic.config import Config

    factory, _, settings, _ = background
    identifier = definition(background)
    ingest(background, START + timedelta(seconds=1))
    ingest(background, START + timedelta(seconds=2))
    advance(factory, settings, now=END)
    with factory.begin() as db:
        original = db.get(DigestDefinition, identifier)
        second = DigestDefinition(
            team_id=DEFAULT_TEAM_ID,
            name="Вторая сводка",
            description="",
            enabled=True,
            version=1,
            configuration=dict(original.configuration),
            created_at=START,
            window_start=START,
            next_end=END,
            window_snapshot={},
        )
        second.window_snapshot = snapshot(second)
        db.add(second)
    monkeypatch.setattr("app.services.digests.BATCH", 1)
    advance(factory, settings, now=END)
    with factory() as db:
        ready = db.scalar(select(DigestRun).where(DigestRun.state == "READY"))
        building = db.scalar(select(DigestRun).where(DigestRun.state == "BUILDING"))
        ready_id, building_id, cutoff = ready.id, building.id, building.cutoff_at
        assert (
            db.scalar(
                select(func.count()).select_from(DigestItem).where(DigestItem.run_id == building_id)
            )
            == 1
        )
    config = Config("alembic.ini")
    with factory.kw["bind"].begin() as conn:
        config.attributes["connection"] = conn
        command.stamp(config, "0010a")
        command.downgrade(config, "0010")
        command.upgrade(config, "0010a")
    with factory() as db:
        assert db.get(DigestRun, building_id).cursor is None
        assert db.get(DigestRun, building_id).cutoff_at == cutoff
        assert (
            db.scalar(
                select(func.count()).select_from(DigestItem).where(DigestItem.run_id == building_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count()).select_from(DigestItem).where(DigestItem.run_id == ready_id)
            )
            == 2
        )
    for _ in range(3):
        advance(factory, settings, now=END)
    with factory() as db:
        assert db.get(DigestRun, building_id).summary["occurrences"] == 2
        assert db.scalar(select(func.count()).select_from(Notification)) == 2
