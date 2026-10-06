from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_identity import create_user, headers, login
from test_notifications import seed
from test_routing import action, save_rule
from test_sources import configured, receipt

from app.adapters.output import DeliveryResult
from app.api.escalation_schemas import PolicyInput
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    EscalationPolicy,
    EscalationRun,
    EscalationStep,
    Event,
    EventAction,
    Notification,
    Team,
)
from app.services.events import stop_for_event
from app.services.events.escalation import advance
from app.services.notifications.delivery import begin, finish, recover
from app.workers.execution import execute
from app.workers.queue import clock, utc


def scenario(background, *, scope="EVENT"):
    factory, store, settings, actor = background
    configured(factory, actor)
    channel = seed(factory, actor, settings)
    with factory.begin() as db:
        policy = EscalationPolicy(
            team_id=DEFAULT_TEAM_ID,
            name="Критическое событие",
            steps=[
                {"channel_id": str(channel), "delay_seconds": delay} for delay in [0, 600, 1800]
            ],
            enabled=True,
            version=1,
        )
        db.add(policy)
        db.flush()
        pid = policy.id
    save_rule(
        factory,
        actor,
        actions=[
            action(fields={"severity": "CRITICAL"}),
            action("ESCALATE", target_id=str(pid), scope=scope),
        ],
    )
    _, work = receipt(factory, store)
    execute(factory, store, settings, str(work))
    with factory() as db:
        run = db.scalar(select(EscalationRun))
        assert run is not None
        return run.id, run.event_id, utc(run.created_at), pid, work


def stop(factory, identifier, status="ACKNOWLEDGED"):
    with factory.begin() as db:
        db.execute(select(Team).where(Team.id == DEFAULT_TEAM_ID).with_for_update()).scalar_one()
        event = db.scalar(select(Event).where(Event.id == identifier).with_for_update())
        event.status, event.version = status, event.version + 1
        stop_for_event(db, event, clock(db))


def test_absolute_deadlines_restarts_failed_first_and_frozen_policy(background):
    factory, store, settings, actor = background
    run_id, event_id, start, policy_id, work = scenario(background)
    with factory.begin() as db:
        row = db.scalar(select(Notification))
        dispatch = begin(db, row.work_id, settings)
    with factory.begin() as db:
        finish(db, dispatch, DeliveryResult("PERMANENT", "AUTHENTICATION_FAILED"))
        policy = db.get(EscalationPolicy, policy_id)
        policy.enabled = False
        policy.steps = [
            {"channel_id": str(db.scalar(select(EscalationStep)).channel_id), "delay_seconds": 0}
        ]
        policy.version += 1
    execute(factory, store, settings, str(work))
    advance(factory, settings, now=start + timedelta(seconds=599))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
    advance(factory, settings, now=start + timedelta(minutes=10))
    advance(factory, settings, now=start + timedelta(minutes=10))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 2
    # New sessions emulate process restarts; deadlines do not move with delivery failures.
    advance(factory, settings, now=start + timedelta(minutes=29, seconds=59))
    advance(factory, settings, now=start + timedelta(minutes=30))
    advance(factory, settings, now=start + timedelta(hours=2))
    with factory() as db:
        run = db.get(EscalationRun, run_id)
        assert run.state == "COMPLETED" and run.snapshot["version"] == 1
        assert db.scalar(select(func.count()).select_from(Notification)) == 3
        steps = db.scalars(select(EscalationStep).order_by(EscalationStep.ordinal)).all()
        assert [int((utc(s.due_at) - start).total_seconds()) for s in steps] == [0, 600, 1800]
        assert len({s.notification_id for s in steps}) == 3


@pytest.mark.parametrize("status", ["ACKNOWLEDGED", "RESOLVED", "SUPPRESSED"])
def test_stop_cancels_pending_and_never_restarts_for_repeats(background, status):
    factory, store, settings, _ = background
    run_id, eid, start, _, _ = scenario(background, scope="OCCURRENCE")
    stop(factory, eid, status)
    advance(factory, settings, now=start + timedelta(hours=1))
    _, work = receipt(factory, store)
    execute(factory, store, settings, str(work))
    with factory.begin() as db:
        run = db.get(EscalationRun, run_id)
        assert run.state == "STOPPED" and run.stop_reason == status
        assert db.scalar(select(func.count()).select_from(EscalationRun)) == 1
        assert [
            s.state for s in db.scalars(select(EscalationStep).order_by(EscalationStep.ordinal))
        ] == ["QUEUED", "CANCELLED", "CANCELLED"]
        row = db.scalar(select(Notification))
        assert row.status == "CANCELLED" and row.attempt_count == 0
        assert begin(db, row.work_id, settings) is None


@pytest.mark.parametrize(
    "outcome,expected", [("ACCEPTED", "SENT"), ("TRANSIENT", "CANCELLED"), ("UNKNOWN", "CANCELLED")]
)
def test_inflight_attempt_finishes_honestly_but_cannot_retry_after_ack(
    background, outcome, expected
):
    factory, _, settings, _ = background
    _, eid, _, _, _ = scenario(background)
    with factory.begin() as db:
        row = db.scalar(select(Notification))
        dispatch = begin(db, row.work_id, settings)
    stop(factory, eid)
    with factory.begin() as db:
        assert finish(db, dispatch, DeliveryResult(outcome, "UNKNOWN_RESULT"))
        row = db.get(Notification, dispatch.identifier)
        assert row.status == expected and row.attempt_count == 1
        assert row.uncertain == (outcome == "UNKNOWN")


def test_recovery_after_ack_cancels_lost_inflight_attempt(background):
    factory, _, settings, _ = background
    _, eid, start, _, _ = scenario(background)
    with factory.begin() as db:
        row = db.scalar(select(Notification))
        dispatch = begin(db, row.work_id, settings)
    stop(factory, eid)
    with factory.begin() as db:
        assert recover(db, 10, now=start + timedelta(minutes=2)) == 1
        assert db.get(Notification, dispatch.identifier).status == "CANCELLED"
        assert not finish(db, dispatch, DeliveryResult("ACCEPTED"))


@pytest.mark.anyio
async def test_operator_actions_idempotency_history_and_terminal_states(background, setup):
    app, factory, password, _, _ = setup
    _, eid, _, _, _ = scenario(background)
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        _, pw = await create_user(client, auth, username="operator", role="OPERATOR")
        auth = headers(await login(client, pw, "operator"))
        body = {"version": 1, "operation_key": str(uuid4())}
        path = f"/api/v1/events/{eid}/actions/acknowledge"
        first = await client.post(path, headers=auth, json=body)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "ACKNOWLEDGED" and first.json()["acknowledged_by"]
        assert (await client.post(path, headers=auth, json=body)).json() == first.json()
        assert (await client.post(path, headers=auth, json={**body, "version": 2})).json()[
            "code"
        ] == "IDEMPOTENCY_CONFLICT"
        resolved = await client.post(
            f"/api/v1/events/{eid}/actions/resolve",
            headers=auth,
            json={"version": 2, "operation_key": str(uuid4())},
        )
        assert resolved.status_code == 200
        assert resolved.json()["acknowledged_at"] == first.json()["acknowledged_at"]
        assert (
            await client.post(
                path, headers=auth, json={"version": 3, "operation_key": str(uuid4())}
            )
        ).json()["code"] == "EVENT_STATE_CONFLICT"
        history = (await client.get(f"/api/v1/events/{eid}/actions")).json()
        assert history["total"] == 2 and "operation_key" not in str(history)
        runs = (await client.get(f"/api/v1/events/{eid}/escalations")).json()
        assert runs["items"][0]["state"] == "STOPPED" and "context" not in str(runs)
        assert (await client.get("/api/v1/escalation-policies")).status_code == 403
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(EventAction)) == 2


@pytest.mark.anyio
async def test_viewer_denied_and_policy_validation_versions(background, setup):
    app, factory, password, actor, settings = setup
    _, eid, _, _, _ = scenario(background)
    with factory() as db:
        channel = db.scalar(select(EscalationStep)).channel_id
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        body = {
            "name": "Дежурство",
            "steps": [{"channel_id": str(channel), "delay_seconds": n} for n in [0, 600, 1800]],
        }
        created = await client.post("/api/v1/escalation-policies", headers=auth, json=body)
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        updated = await client.patch(
            "/api/v1/escalation-policies/" + pid,
            headers=auth,
            json={**body, "version": 1, "enabled": False},
        )
        assert updated.json()["version"] == 2
        versions = (await client.get(f"/api/v1/escalation-policies/{pid}/versions")).json()
        assert versions["total"] == 2 and versions["items"][0]["snapshot"]["enabled"] is False
        invalid = {**body, "steps": [{"channel_id": str(channel), "delay_seconds": 1}]}
        assert (
            await client.post("/api/v1/escalation-policies", headers=auth, json=invalid)
        ).status_code == 422
        _, pw = await create_user(client, auth)
        auth = headers(await login(client, pw, "reader"))
        for action in ["acknowledge", "resolve", "suppress"]:
            assert (
                await client.post(
                    f"/api/v1/events/{eid}/actions/{action}",
                    headers=auth,
                    json={"version": 1, "operation_key": str(uuid4())},
                )
            ).status_code == 403
        assert (await client.get(f"/api/v1/events/{eid}/actions")).status_code == 200


def test_competing_schedulers_create_each_step_once(background):
    factory, _, settings, _ = background
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks")
    _, _, start, _, _ = scenario(background)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: advance(factory, settings, now=start + timedelta(minutes=30)), range(16)
            )
        )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 3


def test_ack_races_with_sender_and_scheduler(background):
    factory, _, settings, _ = background
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks")
    _, eid, start, _, _ = scenario(background)
    with factory() as db:
        work = db.scalar(select(Notification)).work_id
    barrier = Barrier(3)

    def sender():
        barrier.wait()
        with factory.begin() as db:
            return begin(db, work, settings)

    def acknowledge():
        barrier.wait()
        stop(factory, eid)

    def scheduler():
        barrier.wait()
        advance(factory, settings, now=start + timedelta(minutes=30))

    with ThreadPoolExecutor(max_workers=3) as pool:
        sent = pool.submit(sender)
        tasks = [pool.submit(acknowledge), pool.submit(scheduler)]
        dispatch = sent.result(timeout=15)
        for task in tasks:
            task.result(timeout=15)
    if dispatch:
        with factory.begin() as db:
            finish(db, dispatch, DeliveryResult("ACCEPTED"))
    with factory() as db:
        assert db.get(Event, eid).status == "ACKNOWLEDGED"
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.status.in_(["PENDING", "RETRYING", "PROCESSING"]))
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(EscalationStep)
                .where(EscalationStep.state == "WAITING")
            )
            == 0
        )


@pytest.mark.parametrize("delays", [[1], [0, 0], [0, 600, 60], [0, 2592001]])
def test_invalid_deadlines(delays):
    with pytest.raises(ValueError):
        PolicyInput(
            name="Проверка", steps=[{"channel_id": uuid4(), "delay_seconds": v} for v in delays]
        )


@pytest.mark.parametrize(
    "status,expected",
    [("ACKNOWLEDGED", "PENDING"), ("RESOLVED", "PENDING"), ("SUPPRESSED", "CANCELLED")],
)
def test_only_suppression_cancels_ordinary_notifications(background, status, expected):
    from test_notifications import pending

    factory, _, settings, _ = background
    _, eid, _, _, _ = scenario(background)
    with factory() as db:
        cid = db.scalar(select(EscalationStep)).channel_id
    nid, _ = pending(factory, cid, settings, event_id=eid)
    stop(factory, eid, status)
    with factory() as db:
        assert db.get(Notification, nid).status == expected


def test_ack_removes_failed_escalation_from_dead_letters(background):
    factory, _, settings, _ = background
    _, eid, _, _, _ = scenario(background)
    with factory.begin() as db:
        dispatch = begin(db, db.scalar(select(Notification)).work_id, settings)
    with factory.begin() as db:
        finish(db, dispatch, DeliveryResult("PERMANENT", "AUTHENTICATION_FAILED"))
    stop(factory, eid)
    with factory() as db:
        assert db.get(Notification, dispatch.identifier).status == "CANCELLED"


def test_postgres_downgrade_blocks_inflight_and_cancels_queued_escalation(background):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    factory, _, settings, _ = background
    engine = factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL populated migration")
    _, eid, _, _, _ = scenario(background)
    with factory.begin() as db:
        dispatch = begin(db, db.scalar(select(Notification)).work_id, settings)
    config = Config("alembic.ini")
    with pytest.raises(RuntimeError, match="ACTIVE_ESCALATION_DELIVERY"), engine.begin() as conn:
        config.attributes["connection"] = conn
        command.stamp(config, "0009")
        command.downgrade(config, "0008")
    with factory.begin() as db:
        finish(db, dispatch, DeliveryResult("TRANSIENT", "REMOTE_UNAVAILABLE"))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.stamp(config, "0009")
        command.downgrade(config, "0008")
        assert conn.scalar(text("SELECT count(*) FROM notifications WHERE status='CANCELLED'")) == 1
        assert (
            conn.scalar(
                text("SELECT count(*) FROM work_items WHERE kind='DELIVER' AND state='PENDING'")
            )
            == 0
        )
