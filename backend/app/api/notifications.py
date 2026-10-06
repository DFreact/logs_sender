import hashlib
import json
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import func, select

from app.api.channel_schemas import PreviewEvent
from app.api.configuration import audit, scoped
from app.api.dependencies import Actor, Database, require
from app.api.errors import ApiFailure, ErrorCode
from app.api.schemas import InputModel
from app.domain.enums import NotificationStatus
from app.persistence.models import (
    DeliveryAttempt,
    Notification,
    NotificationChannel,
    NotificationOperation,
    WorkItem,
)
from app.security.audit import AuditAction
from app.security.permissions import Permission
from app.services.notifications import TEXT, create, prepare, wake
from app.services.notifications.delivery import blocked
from app.workers.queue import clock, utc

router = APIRouter(prefix="/api/v1")
Reader = Annotated[Actor, Depends(require(Permission.VIEW_EVENTS))]
RetryActor = Annotated[Actor, Depends(require(Permission.RETRY_NOTIFICATIONS))]
CancelActor = Annotated[Actor, Depends(require(Permission.CANCEL_NOTIFICATIONS))]
TestActor = Annotated[Actor, Depends(require(Permission.SEND_TEST_NOTIFICATION))]


class OperationInput(InputModel):
    operation_key: UUID
    version: int = Field(ge=1, strict=True)


class TestInput(InputModel):
    operation_key: UUID
    channel_version: int = Field(ge=1, strict=True)
    event: PreviewEvent = Field(
        default_factory=lambda: PreviewEvent(subject=TEXT["test_subject"], body=TEXT["test_body"])
    )


def view(row):
    return {
        "id": row.id,
        "event_id": row.event_id,
        "channel_name": row.channel_name,
        "channel_id": row.channel_id,
        "channel_version": row.channel_version,
        "kind": row.kind,
        "subject": row.prepared.get("subject", "")
        if row.prepared
        else row.context.get("subject", ""),
        "status": row.status,
        "mode": row.mode,
        "created_at": utc(row.created_at),
        "due_at": utc(row.due_at),
        "finished_at": utc(row.finished_at) if row.finished_at else None,
        "generation": row.generation,
        "attempt_count": row.attempt_count,
        "max_attempts": len(row.retry_delays) + 1,
        "failure_code": row.failure_code,
        "uncertain": row.uncertain,
        "version": row.version,
        "is_test": row.is_test,
        "dead_lettered": row.dead_lettered_at is not None and row.status == "FAILED",
    }


def operation(db, actor, kind, identifier, body):
    raw = {"target": str(identifier), **body.model_dump(mode="json", exclude={"operation_key"})}
    fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    row = db.scalar(
        select(NotificationOperation).where(
            NotificationOperation.team_id == actor.membership.team_id,
            NotificationOperation.operation_key == body.operation_key,
        )
    )
    if row:
        if row.kind != kind or row.fingerprint != fingerprint or row.actor_id != actor.user.id:
            raise ApiFailure(409, ErrorCode.IDEMPOTENCY_CONFLICT)
        return fingerprint, scoped(db, Notification, row.notification_id, actor.membership.team_id)
    return fingerprint, None


def remember(db, actor, kind, body, fingerprint, row):
    db.add(
        NotificationOperation(
            team_id=actor.membership.team_id,
            operation_key=body.operation_key,
            actor_id=actor.user.id,
            kind=kind,
            fingerprint=fingerprint,
            notification_id=row.id,
        )
    )


@router.get("/notifications")
def notifications(
    db: Database,
    actor: Reader,
    status: NotificationStatus | None = None,
    dead_lettered: bool = False,
    event_id: UUID | None = None,
    channel_id: UUID | None = None,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(25, ge=1, le=100),
):
    query = select(Notification).where(Notification.team_id == actor.membership.team_id)
    if status:
        query = query.where(Notification.status == status)
    if dead_lettered:
        query = query.where(
            Notification.status == "FAILED", Notification.dead_lettered_at.is_not(None)
        )
    if event_id:
        query = query.where(Notification.event_id == event_id)
    if channel_id:
        query = query.where(Notification.channel_id == channel_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {"items": [view(row) for row in rows], "total": total}


@router.get("/notifications/{identifier}")
def notification(identifier: UUID, db: Database, actor: Reader):
    row = scoped(db, Notification, identifier, actor.membership.team_id)
    return {**view(row), "prepared": row.prepared, "retry_delays": row.retry_delays}


@router.get("/notifications/{identifier}/attempts")
def attempts(
    identifier: UUID,
    db: Database,
    actor: Reader,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(25, ge=1, le=100),
):
    scoped(db, Notification, identifier, actor.membership.team_id)
    query = select(DeliveryAttempt).where(DeliveryAttempt.notification_id == identifier)
    rows = db.scalars(
        query.order_by(DeliveryAttempt.generation.desc(), DeliveryAttempt.attempt_number.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "generation": r.generation,
                "attempt_number": r.attempt_number,
                "started_at": utc(r.started_at),
                "finished_at": utc(r.finished_at) if r.finished_at else None,
                "status": r.status,
                "failure_code": r.failure_code,
            }
            for r in rows
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
    }


@router.post("/notifications/{identifier}/retry")
def retry(
    identifier: UUID, body: OperationInput, request: Request, db: Database, actor: RetryActor
):
    fingerprint, replay = operation(db, actor, "RETRY", identifier, body)
    if replay:
        return view(replay)
    row = scoped(db, Notification, identifier, actor.membership.team_id)
    # The team lock serializes operators, while this row lock excludes finalizers.
    db.refresh(row, with_for_update=True)
    if row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    from app.services.events import stop_reason

    if stop_reason(db, row):
        raise ApiFailure(409, ErrorCode.NOTIFICATION_STATE_CONFLICT)
    if row.status != "FAILED":
        raise ApiFailure(409, ErrorCode.NOTIFICATION_STATE_CONFLICT)
    row.generation += 1
    row.attempt_count, row.version = 0, row.version + 1
    row.status, row.failure_code = "PENDING", None
    row.finished_at = row.dead_lettered_at = None
    row.due_at = clock(db)
    row.retry_delays = list(request.app.state.settings.delivery_retry_delays)
    row.jitter_percent = request.app.state.settings.delivery_jitter_percent
    channel = scoped(db, NotificationChannel, row.channel_id, row.team_id)
    # Only content that has never been prepared can use a corrected template.
    prepare(db, row, channel)
    if row.status == "PENDING":
        wake(db, row)
    remember(db, actor, "RETRY", body, fingerprint, row)
    audit(db, request, actor, AuditAction.NOTIFICATION_RETRIED, row, "NOTIFICATION")
    db.commit()
    return view(row)


@router.post("/notifications/{identifier}/cancel")
def cancel(
    identifier: UUID, body: OperationInput, request: Request, db: Database, actor: CancelActor
):
    fingerprint, replay = operation(db, actor, "CANCEL", identifier, body)
    if replay:
        return view(replay)
    row = scoped(db, Notification, identifier, actor.membership.team_id)
    db.refresh(row, with_for_update=True)
    if row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    if row.status not in ("PENDING", "RETRYING", "FAILED"):
        raise ApiFailure(409, ErrorCode.NOTIFICATION_STATE_CONFLICT)
    row.status, row.finished_at = "CANCELLED", clock(db)
    row.version += 1
    if row.work_id:
        task = db.get(WorkItem, row.work_id)
        if task and task.state == "PENDING":
            task.state, task.completed_at = "SUCCEEDED", row.finished_at
    remember(db, actor, "CANCEL", body, fingerprint, row)
    audit(db, request, actor, AuditAction.NOTIFICATION_CANCELLED, row, "NOTIFICATION")
    db.commit()
    return view(row)


@router.post("/channels/{identifier}/test-notification", status_code=202)
def test_notification(
    identifier: UUID, body: TestInput, request: Request, db: Database, actor: TestActor
):
    fingerprint, replay = operation(db, actor, "TEST", identifier, body)
    if replay:
        return view(replay)
    channel = scoped(db, NotificationChannel, identifier, actor.membership.team_id)
    if channel.version != body.channel_version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    recent = db.scalar(
        select(func.count())
        .select_from(NotificationOperation)
        .where(
            NotificationOperation.team_id == actor.membership.team_id,
            NotificationOperation.kind == "TEST",
            NotificationOperation.created_at >= clock(db) - timedelta(minutes=1),
        )
    )
    if recent >= 10:
        raise ApiFailure(429, ErrorCode.RATE_LIMITED)
    # Explicit test may use a hidden existing channel, but never a disabled one.
    if blocked(db, channel, channel, request.app.state.settings):
        raise ApiFailure(422, ErrorCode.CHANNEL_UNAVAILABLE)
    row = create(db, channel, request.app.state.settings, body.event.model_dump(), is_test=True)
    remember(db, actor, "TEST", body, fingerprint, row)
    audit(db, request, actor, AuditAction.TEST_NOTIFICATION_CREATED, row, "NOTIFICATION")
    db.commit()
    return view(row)
