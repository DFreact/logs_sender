from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.configuration import Reader, audit, scoped
from app.api.dependencies import CurrentActor, Database
from app.api.errors import ApiFailure, ErrorCode
from app.api.notifications import OperationInput
from app.persistence.models import Event, EventAction, User
from app.security.audit import AuditAction
from app.security.permissions import Permission, permitted
from app.services.events import stop_for_event
from app.workers.queue import clock, utc

router = APIRouter(prefix="/api/v1/events")
ACTIONS = {
    "acknowledge": ("ACKNOWLEDGED", Permission.ACKNOWLEDGE_EVENTS, AuditAction.EVENT_ACKNOWLEDGED),
    "resolve": ("RESOLVED", Permission.RESOLVE_EVENTS, AuditAction.EVENT_RESOLVED),
    "suppress": ("SUPPRESSED", Permission.SUPPRESS_EVENTS, AuditAction.EVENT_SUPPRESSED),
}


def state(db, row):
    actor = db.get(User, row.acknowledged_by) if row.acknowledged_by else None
    return {
        "version": row.version,
        "status": row.status,
        "acknowledged_by": actor.display_name if actor else None,
        "acknowledged_at": utc(row.acknowledged_at) if row.acknowledged_at else None,
    }


@router.get("/{identifier}/actions")
def history(identifier: UUID, db: Database, actor: Reader, offset: int = Query(0, ge=0, le=100000)):
    row = scoped(db, Event, identifier, actor.membership.team_id)
    query = (
        select(EventAction, User.display_name)
        .join(User, User.id == EventAction.actor_id)
        .where(EventAction.event_id == identifier, EventAction.from_status != EventAction.to_status)
    )
    entries = db.execute(
        query.order_by(EventAction.created_at.desc(), EventAction.id.desc())
        .offset(offset)
        .limit(25)
    ).all()
    return {
        **state(db, row),
        "items": [
            {
                "id": entry.id,
                "author": name,
                "from_status": entry.from_status,
                "to_status": entry.to_status,
                "created_at": utc(entry.created_at),
            }
            for entry, name in entries
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
    }


@router.post("/{identifier}/actions/{action}")
def change(
    identifier: UUID,
    action: Literal["acknowledge", "resolve", "suppress"],
    body: OperationInput,
    request: Request,
    db: Database,
    actor: CurrentActor,
):
    target, permission, audit_action = ACTIONS[action]
    if not permitted(actor.membership.role, permission):
        raise ApiFailure(403, ErrorCode.ACCESS_DENIED)
    row = scoped(db, Event, identifier, actor.membership.team_id)
    db.refresh(row, with_for_update=True)
    if row.retiring:
        raise ApiFailure(409, ErrorCode.EVENT_STATE_CONFLICT)
    previous = db.scalar(
        select(EventAction).where(
            EventAction.team_id == row.team_id, EventAction.operation_key == body.operation_key
        )
    )
    if previous:
        if (previous.event_id, previous.actor_id, previous.request_version, previous.to_status) != (
            row.id,
            actor.user.id,
            body.version,
            target,
        ):
            raise ApiFailure(409, ErrorCode.IDEMPOTENCY_CONFLICT)
        return state(db, row)
    before = row.status
    if before != target:
        if row.version != body.version:
            raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
        if before not in ("NEW", "ACKNOWLEDGED") or (target == "ACKNOWLEDGED" and before != "NEW"):
            raise ApiFailure(409, ErrorCode.EVENT_STATE_CONFLICT)
        row.status, row.version = target, row.version + 1
        row.state_changed_at, row.state_changed_by = clock(db), actor.user.id
        if target == "ACKNOWLEDGED":
            row.acknowledged_at, row.acknowledged_by = row.state_changed_at, actor.user.id
        stop_for_event(db, row, row.state_changed_at)
        audit(db, request, actor, audit_action, row, "EVENT")
    db.add(
        EventAction(
            team_id=row.team_id,
            event_id=row.id,
            actor_id=actor.user.id,
            operation_key=body.operation_key,
            request_version=body.version,
            from_status=before,
            to_status=target,
        )
    )
    db.commit()
    return state(db, row)
