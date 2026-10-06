from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.channels import channel_view
from app.api.configuration import Admin, Reader, audit, scoped
from app.api.dependencies import Database
from app.api.errors import ApiFailure, ErrorCode
from app.api.escalation_schemas import PolicyInput
from app.persistence.models import (
    EscalationPolicy,
    EscalationPolicyVersion,
    EscalationRun,
    EscalationStep,
    Event,
    Notification,
    NotificationChannel,
    User,
)
from app.security.audit import AuditAction
from app.workers.queue import utc

router = APIRouter(prefix="/api/v1")


def view(row):
    return {
        key: getattr(row, key)
        for key in ("id", "name", "description", "enabled", "version", "steps")
    }


def save(db, actor, request, body, row=None):
    if row and row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    previous = {s["channel_id"] for s in row.steps} if row else set()
    for step in body.steps:
        channel = scoped(db, NotificationChannel, step.channel_id, actor.membership.team_id)
        if (
            str(channel.id) not in previous
            and not channel_view(db, channel, request.app.state.settings)["available"]
        ):
            raise ApiFailure(422, ErrorCode.ACTION_TARGET_UNAVAILABLE)
    creating = row is None
    values = body.model_dump(mode="json", exclude={"version"})
    if row is None:
        row = EscalationPolicy(team_id=actor.membership.team_id, version=1, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.version += 1
    db.flush()
    db.add(
        EscalationPolicyVersion(
            policy_id=row.id,
            version=row.version,
            actor_id=actor.user.id,
            snapshot=body.model_dump(mode="json") | {"version": row.version},
        )
    )
    audit(
        db,
        request,
        actor,
        AuditAction.ESCALATION_POLICY_CREATED
        if creating
        else AuditAction.ESCALATION_POLICY_UPDATED,
        row,
        "ESCALATION_POLICY",
    )
    db.commit()
    return view(row)


@router.get("/escalation-policies")
def policies(db: Database, actor: Admin):
    rows = db.scalars(
        select(EscalationPolicy)
        .where(EscalationPolicy.team_id == actor.membership.team_id)
        .order_by(EscalationPolicy.name, EscalationPolicy.id)
    ).all()
    return {"items": [view(row) for row in rows], "total": len(rows)}


@router.post("/escalation-policies", status_code=201)
def create_policy(body: PolicyInput, request: Request, db: Database, actor: Admin):
    if (
        db.scalar(
            select(func.count())
            .select_from(EscalationPolicy)
            .where(EscalationPolicy.team_id == actor.membership.team_id)
        )
        >= 100
    ):
        raise ApiFailure(422, ErrorCode.CONFIGURATION_LIMIT)
    return save(db, actor, request, body)


@router.patch("/escalation-policies/{identifier}")
def update_policy(
    identifier: UUID, body: PolicyInput, request: Request, db: Database, actor: Admin
):
    return save(
        db, actor, request, body, scoped(db, EscalationPolicy, identifier, actor.membership.team_id)
    )


@router.get("/escalation-policies/{identifier}/versions")
def versions(identifier: UUID, db: Database, actor: Admin, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, EscalationPolicy, identifier, actor.membership.team_id)
    query = (
        select(EscalationPolicyVersion, User.display_name)
        .join(User, User.id == EscalationPolicyVersion.actor_id)
        .where(EscalationPolicyVersion.policy_id == identifier)
    )
    rows = db.execute(
        query.order_by(EscalationPolicyVersion.version.desc()).offset(offset).limit(25)
    ).all()
    return {
        "items": [
            {
                "version": r.version,
                "author": name,
                "created_at": utc(r.created_at),
                "snapshot": r.snapshot,
            }
            for r, name in rows
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
    }


@router.get("/events/{identifier}/escalations")
def history(identifier: UUID, db: Database, actor: Reader, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, Event, identifier, actor.membership.team_id)
    query = select(EscalationRun).where(EscalationRun.event_id == identifier)
    rows = db.scalars(
        query.order_by(EscalationRun.created_at.desc(), EscalationRun.id.desc())
        .offset(offset)
        .limit(25)
    ).all()
    items = []
    for row in rows:
        steps = []
        for step in db.scalars(
            select(EscalationStep)
            .where(EscalationStep.run_id == row.id)
            .order_by(EscalationStep.ordinal)
        ):
            notification = (
                db.get(Notification, step.notification_id) if step.notification_id else None
            )
            steps.append(
                {
                    "ordinal": step.ordinal,
                    "due_at": utc(step.due_at),
                    "state": step.state,
                    "channel_name": step.channel_name,
                    "notification_id": step.notification_id,
                    "notification_status": notification.status if notification else None,
                }
            )
        items.append(
            {
                "id": row.id,
                "name": row.snapshot["name"],
                "version": row.snapshot["version"],
                "state": row.state,
                "created_at": utc(row.created_at),
                "stopped_at": utc(row.stopped_at) if row.stopped_at else None,
                "stop_reason": row.stop_reason,
                "steps": steps,
            }
        )
    return {"items": items, "total": db.scalar(select(func.count()).select_from(query.subquery()))}
