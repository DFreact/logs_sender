import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_identity import create_user, headers, login
from test_routing import action, save_rule
from test_sources import configured, receipt

from app.adapters.output import DeliveryResult
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    DeliveryAttempt,
    Event,
    Notification,
    NotificationChannel,
    NotificationOperation,
    OutputAdapter,
    Template,
    TemplateVersion,
    WorkItem,
)
from app.security.channel_secrets import protect
from app.services.notifications import create
from app.services.notifications.delivery import begin, finish, recover
from app.services.notifications.delivery import execute as deliver
from app.workers.execution import execute
from app.workers.queue import clock, utc


def seed(factory, actor, settings, *, kind="WEBHOOK", enabled=True):
    with factory.begin() as db:
        template = Template(
            team_id=DEFAULT_TEAM_ID,
            name="Шаблон уведомления",
            kind=kind,
            subject="{{ event.subject }}",
            body="{{ event.severity }}: {{ event.body }}",
            html="",
            version=1,
        )
        db.add(template)
        db.flush()
        db.add(
            TemplateVersion(
                template_id=template.id,
                version=1,
                actor_id=actor,
                snapshot={
                    "kind": kind,
                    "subject": template.subject,
                    "body": template.body,
                    "html": "",
                },
            )
        )
        config = (
            {"kind": "WEBHOOK", "url": "https://192.0.2.10/hook"}
            if kind == "WEBHOOK"
            else {"kind": "TELEGRAM", "chat_id": "-123"}
        )
        channel = NotificationChannel(
            id=uuid4(),
            team_id=DEFAULT_TEAM_ID,
            name="Дежурные",
            kind=kind,
            configuration=config,
            enabled=enabled,
            template_id=template.id,
        )
        if kind == "TELEGRAM":
            channel.secret_ciphertext = protect(
                settings, DEFAULT_TEAM_ID, channel.id, "123:abcdefghijklmnopqrstuvxyz"
            )
        db.add(channel)
        db.flush()
        return channel.id


def pending(factory, channel_id, settings, **kw):
    with factory.begin() as db:
        row = create(
            db,
            db.get(NotificationChannel, channel_id),
            settings,
            {"subject": "Проверка", "body": "Событие", "severity": "Критическая"},
            **kw,
        )
        return row.id, row.work_id


def state(factory, identifier):
    with factory() as db:
        row = db.get(Notification, identifier)
        db.expunge(row)
        return row


def test_routing_prepares_frozen_payload_once_and_never_replays_old_intents(background):
    factory, store, settings, actor = background
    configured(factory, actor)
    channel_id = seed(factory, actor, settings)
    save_rule(
        factory,
        actor,
        actions=[
            action(fields={"severity": "CRITICAL"}),
            action("NOTIFY", target_id=str(channel_id)),
        ],
    )
    _, task = receipt(factory, store)
    execute(factory, store, settings, str(task))
    with factory() as db:
        row = db.scalar(select(Notification))
        assert row.prepared["body"].startswith("Критическая:")
        assert row.context["severity"] == "Критическая" and row.prepared["payload"]
        identifier, work_id = row.id, row.work_id
        snapshot = row.prepared.copy()
        assert row.event_id and row.execution_id and row.template_version_id
    execute(factory, store, settings, str(task))
    _, second = receipt(factory, store)
    execute(factory, store, settings, str(second))
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
        assert db.scalar(select(Event)).occurrence_count == 2
        channel = db.get(NotificationChannel, channel_id)
        template = db.get(Template, channel.template_id)
        template.body = "Изменение шаблона не меняет очередь"
    sent = []
    deliver(
        factory,
        settings,
        work_id,
        send=lambda _, d: sent.append(d.prepared) or DeliveryResult("ACCEPTED"),
    )
    deliver(factory, settings, work_id, send=lambda *_: pytest.fail("duplicate delivery"))
    assert sent == [snapshot] and state(factory, identifier).status == "SENT"


def test_suppression_and_unbound_intents_do_not_create_notifications(background):
    factory, store, settings, actor = background
    configured(factory, actor)
    channel_id = seed(factory, actor, settings)
    save_rule(
        factory, actor, actions=[action("NOTIFY", target_id=str(channel_id)), action("SUPPRESS")]
    )
    _, task = receipt(factory, store)
    execute(factory, store, settings, str(task))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 0


def test_delay_schedule_and_late_wakeup_use_database_time(setup):
    _, factory, _, actor, settings = setup
    cid = seed(factory, actor, settings)
    with factory() as db:
        now = clock(db)
    identifier, work = pending(
        factory, cid, settings, due_at=now + timedelta(minutes=10), mode="DELAYED"
    )
    with factory.begin() as db:
        assert begin(db, work, settings, now=now) is None
        dispatch = begin(db, work, settings, now=now + timedelta(minutes=11))
        assert dispatch
    assert state(factory, identifier).attempt_count == 1


def test_retry_budget_retry_after_unknown_and_stale_result(setup):
    _, factory, _, actor, settings = setup
    settings.delivery_retry_delays, settings.delivery_jitter_percent = [30, 120], 0
    cid = seed(factory, actor, settings)
    identifier, work = pending(factory, cid, settings)
    with factory.begin() as db:
        start = clock(db)
        dispatch = begin(db, work, settings, now=start)
    with factory.begin() as db:
        assert finish(
            db,
            dispatch,
            DeliveryResult("TRANSIENT", "RATE_LIMITED", 300),
            now=start + timedelta(seconds=1),
        )
    row = state(factory, identifier)
    assert row.status == "RETRYING" and utc(row.due_at) == start + timedelta(seconds=301)
    with factory.begin() as db:
        second = begin(db, row.work_id, settings, now=utc(row.due_at))
    with factory.begin() as db:
        assert not finish(db, dispatch, DeliveryResult("ACCEPTED"), now=utc(row.due_at))
        assert (
            recover(
                db, 100, now=utc(row.due_at) + timedelta(seconds=settings.delivery_lease_seconds)
            )
            == 1
        )
    row = state(factory, identifier)
    assert row.uncertain and row.status == "RETRYING" and row.attempt_count == 2
    with factory.begin() as db:
        assert not finish(db, second, DeliveryResult("ACCEPTED"), now=utc(row.due_at))
        third = begin(db, row.work_id, settings, now=utc(row.due_at))
    with factory.begin() as db:
        assert finish(
            db,
            third,
            DeliveryResult("TRANSIENT", "REMOTE_UNAVAILABLE"),
            now=utc(row.due_at) + timedelta(seconds=1),
        )
        attempts = db.scalars(
            select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number)
        ).all()
        assert [a.status for a in attempts] == ["FAILED", "UNKNOWN", "FAILED"]
    row = state(factory, identifier)
    assert row.status == "FAILED" and row.dead_lettered_at and row.attempt_count == 3


def test_disabled_hidden_changed_channels_and_rotated_secret(setup):
    _, factory, _, actor, settings = setup
    cid = seed(factory, actor, settings, enabled=False)
    identifier, work = pending(factory, cid, settings)
    with factory.begin() as db:
        now = clock(db)
        for i in range(4):
            assert begin(db, work, settings, now=now + timedelta(seconds=31 * i)) is None
        assert db.scalar(select(func.count()).select_from(DeliveryAttempt)) == 0
        channel = db.get(NotificationChannel, cid)
        channel.enabled = True
        channel.secret_ciphertext = protect(settings, DEFAULT_TEAM_ID, cid, "rotated-secret")
        channel.secret_version = 2
        db.add(OutputAdapter(team_id=DEFAULT_TEAM_ID, kind="WEBHOOK", enabled=True, visible=False))
    with factory.begin() as db:
        dispatch = begin(db, work, settings, now=now + timedelta(minutes=3))
        assert dispatch.secret == "rotated-secret"
        assert db.scalar(select(DeliveryAttempt)).secret_version == 2
    with factory.begin() as db:
        finish(db, dispatch, DeliveryResult("ACCEPTED"), now=now + timedelta(minutes=3, seconds=1))
    assert state(factory, identifier).status == "SENT"
    identifier, work = pending(factory, cid, settings)
    with factory.begin() as db:
        db.get(NotificationChannel, cid).configuration = {
            "kind": "WEBHOOK",
            "url": "https://192.0.2.10/another",
        }
    with factory.begin() as db:
        assert begin(db, work, settings) is None
    row = state(factory, identifier)
    assert row.failure_code == "CHANNEL_CHANGED" and row.attempt_count == 0


def test_remote_success_before_crash_is_recovered_as_unknown(setup):
    _, factory, _, actor, settings = setup
    cid = seed(factory, actor, settings)
    identifier, work = pending(factory, cid, settings)
    effects = []

    class ProcessDied(BaseException):
        pass

    def send(_, d):
        effects.append(d.idempotency_key)
        raise ProcessDied()

    with pytest.raises(ProcessDied):
        deliver(factory, settings, work, send=send)
    row = state(factory, identifier)
    with factory.begin() as db:
        assert recover(db, 100, now=utc(row.lease_until) + timedelta(seconds=1)) == 1
    row = state(factory, identifier)
    assert row.status == "RETRYING" and row.uncertain
    with factory.begin() as db:
        db.get(Notification, identifier).due_at = clock(db)
        db.get(WorkItem, row.work_id).due_at = clock(db)
    deliver(
        factory,
        settings,
        row.work_id,
        send=lambda _, d: effects.append(d.idempotency_key) or DeliveryResult("ACCEPTED"),
    )
    assert effects == [str(identifier), str(identifier)]
    assert state(factory, identifier).uncertain


def test_permanent_template_failure_is_dead_letter_without_attempt(setup):
    _, factory, _, actor, settings = setup
    cid = seed(factory, actor, settings)
    with factory.begin() as db:
        version = db.scalar(select(TemplateVersion))
        version.snapshot = {**version.snapshot, "body": "{{ event.__class__ }}"}
    identifier, work = pending(factory, cid, settings)
    assert work is None and state(factory, identifier).status == "FAILED"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DeliveryAttempt)) == 0


@pytest.mark.anyio
async def test_manual_operations_are_idempotent_and_preserve_attempts(setup):
    app, factory, password, actor, settings = setup
    cid = seed(factory, actor, settings)
    identifier, work = pending(factory, cid, settings)
    deliver(
        factory,
        settings,
        work,
        send=lambda *_: DeliveryResult("PERMANENT", "AUTHENTICATION_FAILED"),
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        _, pw = await create_user(client, auth, username="operator", role="OPERATOR")
        auth = headers(await login(client, pw, "operator"))
        row = (await client.get(f"/api/v1/notifications/{identifier}")).json()
        body = {"version": row["version"], "operation_key": str(uuid4())}
        first = await client.post(
            f"/api/v1/notifications/{identifier}/retry", headers=auth, json=body
        )
        assert first.status_code == 200 and first.json()["generation"] == 2
        assert (
            await client.post(f"/api/v1/notifications/{identifier}/retry", headers=auth, json=body)
        ).json()["generation"] == 2
        assert (
            await client.post(
                f"/api/v1/notifications/{identifier}/retry",
                headers=auth,
                json={**body, "version": 123},
            )
        ).json()["code"] == "IDEMPOTENCY_CONFLICT"
        attempts = (await client.get(f"/api/v1/notifications/{identifier}/attempts")).json()
        assert attempts["total"] == 1 and attempts["items"][0]["generation"] == 1
        assert "secret" not in json.dumps(attempts).lower() and "lease" not in json.dumps(attempts)
        cancel = {"version": first.json()["version"], "operation_key": str(uuid4())}
        for _ in range(2):
            response = await client.post(
                f"/api/v1/notifications/{identifier}/cancel", headers=auth, json=cancel
            )
            assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
        assert (
            await client.post(
                f"/api/v1/channels/{cid}/test-notification",
                headers=auth,
                json={"channel_version": 1, "operation_key": str(uuid4())},
            )
        ).status_code == 403
    deliver(
        factory,
        settings,
        state(factory, identifier).work_id,
        send=lambda *_: pytest.fail("cancelled notification sent"),
    )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NotificationOperation)) == 2


@pytest.mark.anyio
async def test_test_send_explicit_idempotent_and_viewer_read_only(setup):
    app, factory, password, actor, settings = setup
    cid = seed(factory, actor, settings)
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        auth = headers(await login(client, password))
        body = {"operation_key": str(uuid4()), "channel_version": 1}
        first = await client.post(
            f"/api/v1/channels/{cid}/test-notification", headers=auth, json=body
        )
        assert first.status_code == 202, first.text
        identifier = first.json()["id"]
        assert first.json()["status"] == "PENDING" and first.json()["is_test"]
        assert (
            await client.post(f"/api/v1/channels/{cid}/test-notification", headers=auth, json=body)
        ).json()["id"] == identifier
        _, pw = await create_user(client, auth)
        auth = headers(await login(client, pw, "reader"))
        for path in ("retry", "cancel"):
            assert (
                await client.post(
                    f"/api/v1/notifications/{identifier}/{path}",
                    headers=auth,
                    json={"operation_key": str(uuid4()), "version": 1},
                )
            ).status_code == 403
        response = await client.get(f"/api/v1/notifications/{identifier}")
        assert response.status_code == 200
        assert all(
            v not in response.json() for v in ("configuration", "context", "secret", "lease_token")
        )
        assert (await client.get("/api/v1/notifications?dead_lettered=true")).json()["total"] == 0


def test_concurrent_wakeup_has_one_attempt(setup):
    _, factory, _, actor, settings = setup
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks")
    cid = seed(factory, actor, settings)
    identifier, work = pending(factory, cid, settings)

    def claim(_):
        with factory.begin() as db:
            return begin(db, work, settings)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(claim, range(20)))
    assert sum(r is not None for r in results) == 1
    assert state(factory, identifier).attempt_count == 1
