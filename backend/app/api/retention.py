from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.api.auth import audit_request
from app.api.dependencies import Actor, Database, require
from app.api.errors import ApiFailure, ErrorCode
from app.persistence.models import RetentionPolicy, RetentionProgress, Source
from app.security.audit import AuditAction, record
from app.security.permissions import Permission
from app.services.retention import DEFAULTS, SOURCE_FIELDS, effective

router = APIRouter(prefix="/api/v1/settings/retention")
Admin = Annotated[Actor, Depends(require(Permission.MANAGE_CONFIGURATION))]
Days = Annotated[int, Field(strict=True, ge=1, le=3650)]


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=0)
    enabled: bool
    inherit: bool
    days: dict[str, Days]

    @model_validator(mode="after")
    def valid(self):
        if set(self.days) not in (set(DEFAULTS), SOURCE_FIELDS):
            raise ValueError("RETENTION_FIELDS")
        if self.days["raw_days"] > self.days["event_days"]:
            raise ValueError("RETENTION_ORDER")
        return self


def scope_for(db, actor, source_id):
    team = actor.membership.team_id
    if source_id:
        source = db.get(Source, source_id)
        if not source or source.team_id != team:
            raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    return team, str(source_id) if source_id else "GLOBAL"


def view(db, team, scope):
    row = db.get(RetentionPolicy, (team, scope))
    days, enabled = effective(db, team, None if scope == "GLOBAL" else scope)
    keys = DEFAULTS if scope == "GLOBAL" else SOURCE_FIELDS
    global_policy = db.get(RetentionPolicy, (team, "GLOBAL"))
    states = db.scalars(select(RetentionProgress).where(RetentionProgress.team_id == team)).all()
    return {
        "version": row.version if row else 0,
        "enabled": enabled,
        "inherit": row.inherit if row else scope != "GLOBAL",
        "days": {key: days[key] for key in keys},
        "progress": [
            {
                "kind": s.kind,
                "checked_at": s.checked_at,
                "processed": s.processed,
                "held": s.held,
                "complete": s.complete
                and bool(global_policy and s.revision == global_policy.revision),
            }
            for s in states
        ],
    }


@router.get("")
def get_retention(db: Database, actor: Admin, source_id: UUID | None = None):
    return view(db, *scope_for(db, actor, source_id))


@router.patch("")
def save_retention(
    body: Change, request: Request, db: Database, actor: Admin, source_id: UUID | None = None
):
    team, scope = scope_for(db, actor, source_id)
    expected = set(DEFAULTS) if scope == "GLOBAL" else SOURCE_FIELDS
    if set(body.days) != expected or (scope == "GLOBAL" and body.inherit):
        raise ApiFailure(422, ErrorCode.VALIDATION_ERROR)
    row = db.get(RetentionPolicy, (team, scope))
    if body.version != (row.version if row else 0):
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    global_policy = db.get(RetentionPolicy, (team, "GLOBAL"))
    if not global_policy:
        global_policy = RetentionPolicy(
            team_id=team, scope="GLOBAL", inherit=False, enabled=False, version=0, revision=0
        )
        db.add(global_policy)
        db.flush()
    if row is None:
        row = (
            global_policy
            if scope == "GLOBAL"
            else RetentionPolicy(team_id=team, scope=scope, version=0, revision=0)
        )
        db.add(row)
    before = {
        "retention_enabled": global_policy.enabled,
        "retention_inherit": row.inherit if row.inherit is not None else True,
        **{key: str(value) for key, value in (row.configuration or {}).items()},
    }
    row.version += 1
    row.configuration = body.days
    row.inherit = body.inherit
    if scope == "GLOBAL":
        row.enabled = body.enabled
    global_policy.revision += 1
    record(
        db,
        AuditAction.RETENTION_UPDATED,
        actor_id=actor.user.id,
        entity_type="RETENTION",
        entity_id=source_id or team,
        before=before,
        after={
            "retention_enabled": global_policy.enabled,
            "retention_inherit": body.inherit,
            **{key: str(value) for key, value in body.days.items()},
        },
        **audit_request(request),
    )
    db.commit()
    return view(db, team, scope)
