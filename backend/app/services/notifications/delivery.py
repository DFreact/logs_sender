"""Notification leases own delivery; WorkItems are durable wake-ups only."""

from dataclasses import dataclass, field
from datetime import timedelta
from secrets import randbelow
from uuid import UUID, uuid4

from sqlalchemy import select

from app.adapters.output import DeliveryResult
from app.api.errors import ApiFailure
from app.domain.enums import DeliveryReason
from app.persistence.models import (
    DeliveryAttempt,
    Event,
    Notification,
    NotificationChannel,
    OutputAdapter,
    Team,
    WorkItem,
)
from app.security.channel_secrets import reveal
from app.services.events import cancel_waiting, stop_reason
from app.services.notifications import wake
from app.workers.queue import clock, utc


@dataclass(frozen=True)
class Dispatch:
    identifier: UUID
    token: UUID
    configuration: dict
    prepared: dict
    secret: str = field(repr=False)
    idempotency_key: str


def blocked(db, row, channel, settings):
    if not channel or not channel.enabled:
        return "CHANNEL_DISABLED"
    adapter = db.scalar(
        select(OutputAdapter).where(
            OutputAdapter.team_id == row.team_id, OutputAdapter.kind == row.kind
        )
    )
    if (adapter and not adapter.enabled) or (
        (row.kind == "TELEGRAM" and not settings.telegram_allowed)
        or (row.kind == "MAX" and not settings.max_allowed)
    ):
        return "ADAPTER_DISABLED"
    if channel.configuration != row.configuration:
        return "CHANNEL_CHANGED"
    if not channel.secret_ciphertext and (
        row.kind in ("TELEGRAM", "MAX") or row.configuration.get("username")
    ):
        return "SECRET_UNAVAILABLE"
    return None


def begin(db, work_id, settings, *, now=None):
    task = db.get(WorkItem, work_id)
    if not task or task.kind != "DELIVER":
        return None
    # Configuration/account mutations take the exclusive team lock first.
    db.execute(select(Team).where(Team.id == task.team_id).with_for_update(read=True)).scalar_one()
    candidate = db.get(Notification, task.entity_id)
    if candidate and candidate.event_id:
        db.execute(
            select(Event).where(Event.id == candidate.event_id).with_for_update()
        ).scalar_one()
    task = db.scalar(
        select(WorkItem)
        .where(WorkItem.id == work_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = now or clock(db)
    if task.state != "PENDING" or utc(task.due_at) > now:
        return None
    row = db.scalar(
        select(Notification)
        .where(Notification.id == task.entity_id, Notification.team_id == task.team_id)
        .with_for_update()
    )
    if not row or row.work_id != task.id or row.status not in ("PENDING", "RETRYING"):
        task.state, task.completed_at = "SUCCEEDED", now
        return None
    if utc(row.due_at) > now:
        task.due_at = row.due_at
        return None
    stopped = stop_reason(db, row)
    if stopped:
        cancel_waiting(db, row, stopped, now)
        return None
    channel = db.get(NotificationChannel, row.channel_id)
    reason, secret = blocked(db, row, channel, settings), ""
    if reason is None:
        try:
            secret = reveal(settings, row.team_id, row.channel_id, channel.secret_ciphertext)
        except ApiFailure:
            reason = "SECRET_UNAVAILABLE"
    if reason:
        if row.failure_code != reason:
            row.version += 1
        row.failure_code = reason
        task.due_at = now + timedelta(seconds=30)
        task.publication_at = task.publication_token = None
        return None
    if row.prepared is None or row.attempt_count >= len(row.retry_delays) + 1:
        row.status, row.failure_code = "FAILED", "TEMPLATE_INVALID"
        row.finished_at = row.dead_lettered_at = now
        row.version += 1
        task.state, task.completed_at = "SUCCEEDED", now
        return None
    row.status, row.lease_token = "PROCESSING", uuid4()
    row.lease_until = now + timedelta(seconds=settings.delivery_lease_seconds)
    row.attempt_count += 1
    row.version += 1
    row.failure_code = None
    db.add(
        DeliveryAttempt(
            notification_id=row.id,
            generation=row.generation,
            attempt_number=row.attempt_count,
            started_at=now,
            status="PROCESSING",
            lease_token=row.lease_token,
            secret_version=channel.secret_version,
        )
    )
    # Wake-up acknowledged atomically with a recoverable notification lease.
    task.state, task.completed_at = "SUCCEEDED", now
    task.attempts += 1
    task.publication_at = task.publication_token = None
    return Dispatch(row.id, row.lease_token, row.configuration, row.prepared, secret, str(row.id))


def finish_row(db, row, result, now):
    attempt = db.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.notification_id == row.id,
            DeliveryAttempt.lease_token == row.lease_token,
        )
        .with_for_update()
    )
    reason = result.reason if result.reason in DeliveryReason else "UNKNOWN_RESULT"
    attempt.status = (
        "SENT"
        if result.outcome == "ACCEPTED"
        else "UNKNOWN"
        if result.outcome == "UNKNOWN"
        else "FAILED"
    )
    attempt.failure_code = None if result.outcome == "ACCEPTED" else reason
    attempt.finished_at = now
    row.lease_token = row.lease_until = None
    row.version += 1
    row.failure_code = attempt.failure_code
    row.uncertain = row.uncertain or result.outcome == "UNKNOWN"
    if result.outcome == "ACCEPTED":
        row.status, row.finished_at = "SENT", now
    elif stopped := stop_reason(db, row):
        row.status, row.finished_at, row.failure_code = "CANCELLED", now, stopped
    elif result.outcome == "PERMANENT" or row.attempt_count >= len(row.retry_delays) + 1:
        row.status, row.finished_at, row.dead_lettered_at = "FAILED", now, now
    else:
        delay = row.retry_delays[row.attempt_count - 1]
        jitter = randbelow(max(1, delay * row.jitter_percent // 100 + 1))
        row.due_at = now + timedelta(
            seconds=max(delay + jitter, min(3600, max(0, result.retry_after or 0)))
        )
        row.status = "RETRYING"
        wake(db, row)


def finish(db, dispatch, result, *, now=None):
    initial = db.get(Notification, dispatch.identifier)
    if initial is None:
        return False
    db.execute(
        select(Team).where(Team.id == initial.team_id).with_for_update(read=True)
    ).scalar_one()
    row = db.scalar(
        select(Notification)
        .where(Notification.id == dispatch.identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = now or clock(db)
    if (
        not row
        or row.status != "PROCESSING"
        or row.lease_token != dispatch.token
        or utc(row.lease_until) <= now
    ):
        return False
    finish_row(db, row, result, now)
    return True


def recover(db, limit, *, now=None):
    now = now or clock(db)
    teams = db.scalars(
        select(Notification.team_id)
        .where(Notification.status == "PROCESSING", Notification.lease_until <= now)
        .distinct()
        .order_by(Notification.team_id)
    ).all()
    for team in teams:
        db.execute(select(Team).where(Team.id == team).with_for_update(read=True)).scalar_one()
    rows = db.scalars(
        select(Notification)
        .where(Notification.status == "PROCESSING", Notification.lease_until <= now)
        .order_by(Notification.lease_until, Notification.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        finish_row(db, row, DeliveryResult("UNKNOWN", "UNKNOWN_RESULT"), now)
    return len(rows)


def execute(factory, settings, work_id, *, send=None):
    from app.adapters.output.send import send_isolated

    with factory.begin() as db:
        dispatch = begin(db, work_id, settings)
    if dispatch is None:
        return
    result = (send or send_isolated)(settings, dispatch)
    with factory.begin() as db:
        finish(db, dispatch, result)
