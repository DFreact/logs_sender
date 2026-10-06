from datetime import timedelta
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.channels import channel_view
from app.api.configuration import Admin, Reader, audit, scoped
from app.api.dependencies import Database
from app.api.digest_schemas import DigestInput
from app.api.errors import ApiFailure, ErrorCode
from app.api.notifications import OperationInput
from app.persistence.models import (
    DigestDefinition,
    DigestItem,
    DigestReceipt,
    DigestRun,
    DigestVersion,
    Notification,
    NotificationChannel,
    Source,
    Template,
    User,
)
from app.security.audit import AuditAction
from app.services.digests import pending, receipts, snapshot
from app.services.digests.schedule import next_boundary, window_start
from app.workers.queue import clock, utc

router = APIRouter(prefix="/api/v1/digests")


def view(row):
    return {
        "id": row.id,
        **snapshot(row),
        "created_at": utc(row.created_at),
        "window_start": utc(row.window_start),
        "next_end": utc(row.next_end),
    }


def save(db, actor, request, body, row=None):
    if row and row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    now = clock(db)
    if row and utc(row.next_end) <= now:
        raise ApiFailure(409, ErrorCode.DIGEST_BACKLOG)
    team = actor.membership.team_id
    channel = scoped(db, NotificationChannel, body.channel_id, team)
    if (
        row is None or str(body.channel_id) != row.configuration["channel_id"]
    ) and not channel_view(db, channel, request.app.state.settings)["available"]:
        raise ApiFailure(422, ErrorCode.ACTION_TARGET_UNAVAILABLE)
    template = scoped(db, Template, body.template_id, team)
    if template.kind != channel.kind:
        raise ApiFailure(422, ErrorCode.TEMPLATE_INVALID)
    for source in body.source_ids:
        scoped(db, Source, source, team)
    values = body.model_dump(mode="json")
    config = {
        k: v for k, v in values.items() if k not in ("name", "description", "version", "enabled")
    }
    creating = row is None
    if creating:
        if (
            db.scalar(
                select(func.count())
                .select_from(DigestDefinition)
                .where(DigestDefinition.team_id == team)
            )
            >= 100
        ):
            raise ApiFailure(422, ErrorCode.CONFIGURATION_LIMIT)
        row = DigestDefinition(
            team_id=team,
            created_at=now,
            window_start=now,
            name=body.name,
            description=body.description,
            enabled=body.enabled,
            version=1,
            configuration=config,
            next_end=next_boundary(now, config),
            window_snapshot={**values, "version": 1},
        )
        db.add(row)
    else:
        row.name, row.description, row.enabled = body.name, body.description, body.enabled
        row.configuration, row.version = config, row.version + 1
    db.flush()
    db.add(
        DigestVersion(
            definition_id=row.id,
            version=row.version,
            actor_id=actor.user.id,
            snapshot=snapshot(row),
        )
    )
    audit(
        db,
        request,
        actor,
        AuditAction.DIGEST_CREATED if creating else AuditAction.DIGEST_UPDATED,
        row,
        "DIGEST",
    )
    db.commit()
    return view(row)


@router.get("")
def definitions(db: Database, actor: Reader):
    rows = db.scalars(
        select(DigestDefinition)
        .where(DigestDefinition.team_id == actor.membership.team_id)
        .order_by(DigestDefinition.name, DigestDefinition.id)
    ).all()
    return {"items": [view(row) for row in rows], "total": len(rows)}


@router.post("", status_code=201)
def create_definition(body: DigestInput, db: Database, actor: Admin, request: Request):
    return save(db, actor, request, body)


@router.patch("/{identifier}")
def update_definition(
    identifier: UUID, body: DigestInput, db: Database, actor: Admin, request: Request
):
    return save(
        db, actor, request, body, scoped(db, DigestDefinition, identifier, actor.membership.team_id)
    )


@router.get("/{identifier}/versions")
def versions(
    identifier: UUID, db: Database, actor: Reader, offset: int = Query(0, ge=0, le=100000)
):
    scoped(db, DigestDefinition, identifier, actor.membership.team_id)
    query = (
        select(DigestVersion, User.display_name)
        .join(User, User.id == DigestVersion.actor_id)
        .where(DigestVersion.definition_id == identifier)
    )
    rows = db.execute(query.order_by(DigestVersion.version.desc()).offset(offset).limit(25)).all()
    return {
        "items": [
            {
                "version": r.version,
                "author": author,
                "created_at": utc(r.created_at),
                "snapshot": r.snapshot,
            }
            for r, author in rows
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
    }


def run_view(db, run):
    notification = db.get(Notification, run.notification_id) if run.notification_id else None
    late = (
        db.scalar(select(func.count()).select_from(receipts(run, late=True).subquery()))
        if run.partial and run.cutoff_at
        else 0
    )
    return {
        "id": run.id,
        "name": run.snapshot["name"],
        "definition_version": run.snapshot["version"],
        "state": run.state,
        "version": run.version,
        "window_start": utc(run.window_start),
        "window_end": utc(run.window_end),
        "review_at": utc(run.review_at),
        "partial": run.partial,
        "pending_count": pending(db, run),
        "missing_at_cutoff": run.pending_count,
        "late_count": late,
        "summary": run.summary,
        "notification_id": run.notification_id,
        "notification_status": notification.status if notification else None,
    }


@router.get("/{identifier}/runs")
def runs(identifier: UUID, db: Database, actor: Reader, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, DigestDefinition, identifier, actor.membership.team_id)
    query = select(DigestRun).where(DigestRun.definition_id == identifier)
    rows = db.scalars(
        query.order_by(DigestRun.window_end.desc(), DigestRun.id.desc()).offset(offset).limit(25)
    ).all()
    return {
        "items": [run_view(db, r) for r in rows],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
    }


@router.get("/runs/{identifier}/items")
def items(
    identifier: UUID,
    db: Database,
    actor: Reader,
    late: bool = False,
    offset: int = Query(0, ge=0, le=100000),
):
    run = scoped(db, DigestRun, identifier, actor.membership.team_id)
    if late:
        if not run.partial or not run.cutoff_at:
            return {"items": [], "total": 0}
        query = receipts(run, late=True)
        rows = db.scalars(
            query.order_by("received_at", "occurrence_id").offset(offset).limit(25)
        ).all()
        result = [
            {
                "event_id": r.event_id,
                "subject": r.snapshot["subject"],
                "received_at": utc(r.received_at),
            }
            for r in rows
        ]
    else:
        query = select(DigestItem).where(DigestItem.run_id == identifier)
        rows = db.scalars(
            query.order_by(DigestItem.received_at, DigestItem.occurrence_id)
            .offset(offset)
            .limit(25)
        ).all()
        result = [
            {"event_id": r.event_id, "subject": r.subject, "received_at": utc(r.received_at)}
            for r in rows
        ]
    return {"items": result, "total": db.scalar(select(func.count()).select_from(query.subquery()))}


@router.post("/runs/{identifier}/{action}")
def decision(
    identifier: UUID,
    action: Literal["wait", "partial"],
    body: OperationInput,
    db: Database,
    actor: Admin,
    request: Request,
):
    run = scoped(db, DigestRun, identifier, actor.membership.team_id)
    if run.operation_key == body.operation_key:
        if (run.operation_version, run.operation_action, run.operation_actor) != (
            body.version,
            action,
            actor.user.id,
        ):
            raise ApiFailure(409, ErrorCode.IDEMPOTENCY_CONFLICT)
        return run_view(db, run)
    if run.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    if run.state != "ATTENTION":
        raise ApiFailure(409, ErrorCode.DIGEST_STATE_CONFLICT)
    now = clock(db)
    run.operation_actor = actor.user.id
    run.partial = action == "partial"
    run.state = "WAITING"
    run.review_at = now + timedelta(seconds=run.snapshot["wait_seconds"])
    run.version += 1
    run.operation_key, run.operation_version, run.operation_action = (
        body.operation_key,
        body.version,
        action,
    )
    # Freeze partial cutoff under the same lock as ingestion and normalization.
    if run.partial:
        run.cutoff_at, run.state, run.pending_count = now, "BUILDING", pending(db, run)
    audit(
        db,
        request,
        actor,
        AuditAction.DIGEST_PARTIAL if run.partial else AuditAction.DIGEST_WAIT,
        db.get(DigestDefinition, run.definition_id),
        "DIGEST",
    )
    db.commit()
    return run_view(db, run)


@router.get("/{identifier}/preview")
def preview(identifier: UUID, db: Database, actor: Reader):
    row = scoped(db, DigestDefinition, identifier, actor.membership.team_id)
    # Preview has no writes and uses the already frozen current window configuration.
    run = SimpleNamespace(
        team_id=row.team_id,
        definition_id=row.id,
        snapshot=row.window_snapshot,
        window_start=window_start(
            row.window_start, row.next_end, row.created_at, row.window_snapshot
        ),
        window_end=row.next_end,
        cutoff_at=None,
    )
    query = receipts(run)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    count_events = db.scalar(select(func.count(func.distinct(query.subquery().c.event_id))))
    rows = db.scalars(
        query.order_by(DigestReceipt.received_at, DigestReceipt.occurrence_id).limit(25)
    ).all()
    return {
        "window_start": utc(run.window_start),
        "window_end": utc(run.window_end),
        "occurrences": total,
        "events": count_events,
        "pending_count": pending(db, run),
        "items": [
            {
                "event_id": r.event_id,
                "subject": r.snapshot["subject"],
                "received_at": utc(r.received_at),
            }
            for r in rows
        ],
    }
