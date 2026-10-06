from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.auth import audit_request
from app.api.config_schemas import DEFAULT_FIELDS, PolicyInput, RuleInput, SourceInput
from app.api.dependencies import Actor, Database, require
from app.api.errors import ApiFailure, ErrorCode
from app.persistence.models import DedupPolicy, IdentificationRule, RuleVersion, Source, User
from app.security.audit import AuditAction, record
from app.security.permissions import Permission
from app.workers.queue import utc

router = APIRouter(prefix="/api/v1")
Admin = Annotated[Actor, Depends(require(Permission.MANAGE_CONFIGURATION))]
Reader = Annotated[Actor, Depends(require(Permission.VIEW_EVENTS))]


def scoped(db, model, identifier, team_id):
    row = db.scalar(select(model).where(model.id == identifier, model.team_id == team_id))
    if row is None:
        raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    return row


def policy_view(row, inherit=False):
    return (
        {
            key: getattr(row, key)
            for key in ("enabled", "inherit", "window_seconds", "fields", "version")
        }
        if row
        else {
            "enabled": False,
            "inherit": inherit,
            "window_seconds": 600,
            "fields": DEFAULT_FIELDS,
            "version": 1,
        }
    )


def policy_for(db, team_id, scope):
    return db.scalar(
        select(DedupPolicy).where(DedupPolicy.team_id == team_id, DedupPolicy.scope == scope)
    )


def save_policy(db, team_id, scope, body):
    row = policy_for(db, team_id, scope)
    if body.version != (row.version if row else 1):
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    values = body.model_dump(exclude={"version"})
    if row is None:
        row = DedupPolicy(team_id=team_id, scope=scope, version=2, **values)
        db.add(row)
    elif any(getattr(row, key) != value for key, value in values.items()):
        for key, value in values.items():
            setattr(row, key, value)
        row.version += 1
    db.flush()
    return row


def source_view(db, row):
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "enabled": row.enabled,
        "version": row.version,
        "dedup": policy_view(policy_for(db, row.team_id, str(row.id)), inherit=True),
    }


@router.get("/sources")
def sources(db: Database, actor: Reader):
    rows = db.scalars(
        select(Source)
        .where(Source.team_id == actor.membership.team_id)
        .order_by(Source.name, Source.id)
    ).all()
    return {"items": [source_view(db, row) for row in rows], "total": len(rows)}


def audit(db, request, actor, action, row, entity_type):
    record(
        db,
        action,
        actor_id=actor.user.id,
        entity_id=row.id,
        entity_type=entity_type,
        after={"display_name": row.name} if hasattr(row, "name") else {},
        **audit_request(request),
    )


@router.post("/sources", status_code=201)
def create_source(body: SourceInput, request: Request, db: Database, actor: Admin):
    if (
        db.scalar(
            select(func.count())
            .select_from(Source)
            .where(Source.team_id == actor.membership.team_id)
        )
        >= 100
    ):
        raise ApiFailure(422, ErrorCode.CONFIGURATION_LIMIT)
    row = Source(team_id=actor.membership.team_id, **body.model_dump(exclude={"dedup", "version"}))
    db.add(row)
    try:
        db.flush()
        save_policy(db, row.team_id, str(row.id), body.dedup)
        audit(db, request, actor, AuditAction.SOURCE_CREATED, row, "SOURCE")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiFailure(409, ErrorCode.SOURCE_NAME_TAKEN) from None
    return source_view(db, row)


@router.patch("/sources/{identifier}")
def update_source(
    identifier: UUID, body: SourceInput, request: Request, db: Database, actor: Admin
):
    row = scoped(db, Source, identifier, actor.membership.team_id)
    if row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    row.name, row.description, row.enabled = body.name, body.description, body.enabled
    row.version += 1
    try:
        save_policy(db, row.team_id, str(row.id), body.dedup)
        audit(db, request, actor, AuditAction.SOURCE_UPDATED, row, "SOURCE")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiFailure(409, ErrorCode.SOURCE_NAME_TAKEN) from None
    return source_view(db, row)


@router.get("/dedup-policy")
def global_policy(db: Database, actor: Admin):
    return policy_view(policy_for(db, actor.membership.team_id, "GLOBAL"))


@router.patch("/dedup-policy")
def update_global_policy(body: PolicyInput, request: Request, db: Database, actor: Admin):
    if body.inherit:
        raise ApiFailure(422, ErrorCode.VALIDATION_ERROR)
    row = save_policy(db, actor.membership.team_id, "GLOBAL", body)
    audit(db, request, actor, AuditAction.DEDUP_POLICY_UPDATED, row, "DEDUP_POLICY")
    db.commit()
    return policy_view(row)


def rule_view(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "name",
            "source_id",
            "priority",
            "enabled",
            "version",
            "conditions",
            "assignments",
        )
    }


@router.get("/identification-rules")
def rules(db: Database, actor: Admin):
    rows = db.scalars(
        select(IdentificationRule)
        .where(IdentificationRule.team_id == actor.membership.team_id)
        .order_by(IdentificationRule.priority, IdentificationRule.id)
    ).all()
    return {"items": [rule_view(row) for row in rows], "total": len(rows)}


def save_rule(db, actor, request, body, row=None):
    scoped(db, Source, body.source_id, actor.membership.team_id)
    if row and row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    values = body.model_dump(exclude={"version"}, mode="json")
    values["source_id"] = body.source_id
    creating = row is None
    if creating:
        row = IdentificationRule(team_id=actor.membership.team_id, version=1, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.version += 1
    db.flush()
    snapshot = body.model_dump(mode="json")
    snapshot["version"] = row.version
    db.add(
        RuleVersion(rule_id=row.id, version=row.version, actor_id=actor.user.id, snapshot=snapshot)
    )
    audit(
        db,
        request,
        actor,
        AuditAction.IDENTIFICATION_RULE_CREATED
        if creating
        else AuditAction.IDENTIFICATION_RULE_UPDATED,
        row,
        "IDENTIFICATION_RULE",
    )
    db.commit()
    return rule_view(row)


@router.post("/identification-rules", status_code=201)
def create_rule(body: RuleInput, request: Request, db: Database, actor: Admin):
    if (
        db.scalar(
            select(func.count())
            .select_from(IdentificationRule)
            .where(IdentificationRule.team_id == actor.membership.team_id)
        )
        >= 100
    ):
        raise ApiFailure(422, ErrorCode.CONFIGURATION_LIMIT)
    return save_rule(db, actor, request, body)


@router.patch("/identification-rules/{identifier}")
def update_rule(identifier: UUID, body: RuleInput, request: Request, db: Database, actor: Admin):
    return save_rule(
        db,
        actor,
        request,
        body,
        scoped(db, IdentificationRule, identifier, actor.membership.team_id),
    )


@router.get("/identification-rules/{identifier}/versions")
def versions(
    identifier: UUID,
    db: Database,
    actor: Admin,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
):
    scoped(db, IdentificationRule, identifier, actor.membership.team_id)
    rows = db.execute(
        select(RuleVersion, User.display_name)
        .join(User, User.id == RuleVersion.actor_id)
        .where(RuleVersion.rule_id == identifier)
        .order_by(RuleVersion.version.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "version": row.version,
                "author": author,
                "created_at": utc(row.created_at),
                "snapshot": row.snapshot,
            }
            for row, author in rows
        ],
        "total": db.scalar(
            select(func.count()).select_from(RuleVersion).where(RuleVersion.rule_id == identifier)
        ),
    }
