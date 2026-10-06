from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.channels import channel_view
from app.api.configuration import Admin, audit, scoped
from app.api.dependencies import Database
from app.api.errors import ApiFailure, ErrorCode
from app.api.routing_schemas import RoutingInput, SimulationInput
from app.persistence.models import (
    DigestDefinition,
    EscalationPolicy,
    NotificationChannel,
    RoutingRule,
    RuleVersion,
    Source,
    User,
    utcnow,
)
from app.security.audit import AuditAction
from app.services.simulation import simulate
from app.workers.queue import utc

router = APIRouter(prefix="/api/v1")


def view(row):
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "name",
                "description",
                "priority",
                "enabled",
                "version",
                "conditions",
                "actions",
            )
        },
        "created_at": utc(row.created_at),
        "updated_at": utc(row.updated_at),
    }


def references(db, team_id, body, settings, existing=None):
    pending = [body.conditions]
    while pending:
        node = pending.pop()
        if "group" in node:
            pending.extend(node["children"])
        elif node["field"] == "source" and node["operator"] != "exists":
            scoped(db, Source, UUID(node["value"]), team_id)
    previous = {
        a.get("target_id")
        for a in (existing.actions if existing else [])
        if a.get("kind") == "NOTIFY"
    }
    for action in body.actions:
        target = getattr(action, "target_id", None)
        if target is None:
            continue
        if action.kind in ("ESCALATE", "DIGEST"):
            policy = scoped(
                db,
                EscalationPolicy if action.kind == "ESCALATE" else DigestDefinition,
                target,
                team_id,
            )
            old = {
                a.get("target_id")
                for a in (existing.actions if existing else [])
                if a.get("kind") == action.kind
            }
            if not policy.enabled and str(target) not in old:
                raise ApiFailure(422, ErrorCode.ACTION_TARGET_UNAVAILABLE)
            continue
        if action.kind != "NOTIFY":
            raise ApiFailure(422, ErrorCode.ACTION_TARGET_UNAVAILABLE)
        channel = scoped(db, NotificationChannel, target, team_id)
        # Preserve existing references when an adapter is hidden/disabled.
        if str(target) not in previous and not channel_view(db, channel, settings)["available"]:
            raise ApiFailure(422, ErrorCode.ACTION_TARGET_UNAVAILABLE)


def save(db, actor, request, body, row=None):
    references(db, actor.membership.team_id, body, request.app.state.settings, row)
    creating = row is None
    if row and row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    values = body.model_dump(exclude={"version"}, mode="json")
    if creating:
        row = RoutingRule(team_id=actor.membership.team_id, version=1, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.version += 1
        row.updated_at = utcnow()
    db.flush()
    snapshot = {**body.model_dump(mode="json"), "version": row.version}
    db.add(
        RuleVersion(
            routing_rule_id=row.id, version=row.version, actor_id=actor.user.id, snapshot=snapshot
        )
    )
    audit(
        db,
        request,
        actor,
        AuditAction.ROUTING_RULE_CREATED if creating else AuditAction.ROUTING_RULE_UPDATED,
        row,
        "ROUTING_RULE",
    )
    db.commit()
    return view(row)


@router.get("/routing-rules")
def rules(db: Database, actor: Admin):
    rows = db.scalars(
        select(RoutingRule)
        .where(RoutingRule.team_id == actor.membership.team_id)
        .order_by(RoutingRule.priority, RoutingRule.id)
    ).all()
    return {"items": [view(row) for row in rows], "total": len(rows)}


@router.post("/routing-rules", status_code=201)
def create_rule(body: RoutingInput, request: Request, db: Database, actor: Admin):
    if (
        db.scalar(
            select(func.count())
            .select_from(RoutingRule)
            .where(RoutingRule.team_id == actor.membership.team_id)
        )
        >= 100
    ):
        raise ApiFailure(422, ErrorCode.CONFIGURATION_LIMIT)
    return save(db, actor, request, body)


@router.patch("/routing-rules/{identifier}")
def update_rule(identifier: UUID, body: RoutingInput, request: Request, db: Database, actor: Admin):
    return save(
        db, actor, request, body, scoped(db, RoutingRule, identifier, actor.membership.team_id)
    )


@router.get("/routing-rules/{identifier}/versions")
def versions(
    identifier: UUID,
    db: Database,
    actor: Admin,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(25, ge=1, le=100),
):
    scoped(db, RoutingRule, identifier, actor.membership.team_id)
    rows = db.execute(
        select(RuleVersion, User.display_name)
        .join(User, User.id == RuleVersion.actor_id)
        .where(RuleVersion.routing_rule_id == identifier)
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
            select(func.count())
            .select_from(RuleVersion)
            .where(RuleVersion.routing_rule_id == identifier)
        ),
    }


@router.post("/rules/validate")
def validate_rule(body: RoutingInput, db: Database, actor: Admin, request: Request):
    references(db, actor.membership.team_id, body, request.app.state.settings)
    return {"valid": True}


@router.post("/rules/simulate")
def simulation(body: SimulationInput, db: Database, actor: Admin):
    return simulate(db, actor.membership.team_id, body)
