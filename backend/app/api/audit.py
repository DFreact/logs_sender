from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.api.dependencies import Actor, Database, require
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    AuditEntry,
    DigestDefinition,
    EscalationPolicy,
    Event,
    IdentificationRule,
    IngestCredential,
    Notification,
    NotificationChannel,
    RoutingRule,
    Source,
    Template,
    User,
)
from app.security.audit import AuditAction, safe_snapshot
from app.security.permissions import Permission

router = APIRouter(prefix="/api/v1/audit")


@router.get("")
def list_audit(
    db: Database,
    actor: Annotated[Actor, Depends(require(Permission.VIEW_AUDIT))],
    action: AuditAction | None = None,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
):
    target = aliased(User)
    query = (
        select(
            AuditEntry,
            User.display_name,
            func.coalesce(
                target.display_name,
                IngestCredential.name,
                Source.name,
                IdentificationRule.name,
                RoutingRule.name,
                NotificationChannel.name,
                Notification.channel_name,
                Template.name,
                Event.subject,
                EscalationPolicy.name,
                DigestDefinition.name,
            ),
        )
        .outerjoin(
            User,
            User.id == AuditEntry.actor_id,
        )
        .outerjoin(target, target.id == AuditEntry.entity_id)
        .outerjoin(
            IngestCredential,
            (IngestCredential.id == AuditEntry.entity_id)
            & (AuditEntry.entity_type == "INGEST_CREDENTIAL"),
        )
        .outerjoin(
            Source,
            (Source.id == AuditEntry.entity_id)
            & (AuditEntry.entity_type.in_(["SOURCE", "RETENTION"])),
        )
        .outerjoin(
            IdentificationRule,
            (IdentificationRule.id == AuditEntry.entity_id)
            & (AuditEntry.entity_type == "IDENTIFICATION_RULE"),
        )
        .where(
            AuditEntry.team_id == DEFAULT_TEAM_ID,
        )
        .outerjoin(
            RoutingRule,
            (RoutingRule.id == AuditEntry.entity_id) & (AuditEntry.entity_type == "ROUTING_RULE"),
        )
    )
    query = query.outerjoin(
        NotificationChannel,
        (NotificationChannel.id == AuditEntry.entity_id) & (AuditEntry.entity_type == "CHANNEL"),
    ).outerjoin(
        Template, (Template.id == AuditEntry.entity_id) & (AuditEntry.entity_type == "TEMPLATE")
    )
    query = query.outerjoin(
        Notification,
        (Notification.id == AuditEntry.entity_id) & (AuditEntry.entity_type == "NOTIFICATION"),
    )
    query = query.outerjoin(
        Event, (Event.id == AuditEntry.entity_id) & (AuditEntry.entity_type == "EVENT")
    ).outerjoin(
        EscalationPolicy,
        (EscalationPolicy.id == AuditEntry.entity_id)
        & (AuditEntry.entity_type == "ESCALATION_POLICY"),
    )
    query = query.outerjoin(
        DigestDefinition,
        (DigestDefinition.id == AuditEntry.entity_id) & (AuditEntry.entity_type == "DIGEST"),
    )
    if action is not None:
        query = query.where(AuditEntry.action == action)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(AuditEntry.timestamp.desc(), AuditEntry.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": entry.id,
                "timestamp": entry.timestamp,
                "actor": actor_name,
                "target": target_name,
                "action": entry.action,
                "entity_type": entry.entity_type,
                "before": safe_snapshot(entry.before),
                "after": safe_snapshot(entry.after),
                "request_ip": entry.request_ip,
                "request_id": entry.request_id,
            }
            for entry, actor_name, target_name in rows
        ],
    }
