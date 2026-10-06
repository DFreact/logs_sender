"""Administrator-only connection instructions and scoped ingestion key lifecycle."""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import func, select

from app.api.auth import audit_request
from app.api.config_schemas import RuleInput
from app.api.configuration import Admin, scoped
from app.api.dependencies import Database
from app.api.errors import ApiFailure, ErrorCode
from app.api.schemas import InputModel
from app.persistence.models import IngestCredential
from app.security.audit import AuditAction, record
from app.services.ingestion import issue_key
from app.workers.queue import clock, utc

router = APIRouter(prefix="/api/v1/connections")


class KeyInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    name_valid = field_validator("name")(RuleInput.name_valid.__func__)


def key_view(row, now):
    return {
        "id": row.id,
        "name": row.name,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "status": "REVOKED"
        if row.revoked_at
        else ("EXPIRED" if utc(row.expires_at) <= now else "ACTIVE"),
    }


@router.get("")
def instructions(request: Request, actor: Admin):
    settings = request.app.state.settings
    # Published host/port must be supplied by the installer: container bind addresses
    # and untrusted Host / Forwarded headers are not connection instructions.
    return {
        "rest_path": "/api/v1/ingest",
        "max_bytes": min(settings.ingest_max_bytes, settings.blob_max_bytes),
        "per_minute": settings.ingest_per_minute,
        "smtp": {
            "host": settings.smtp_public_host,
            "port": settings.smtp_public_port,
            "recipients": settings.smtp_recipients,
            "tls_required": not settings.smtp_allow_plaintext,
        },
    }


@router.get("/keys")
def keys(db: Database, actor: Admin, offset: int = Query(0, ge=0, le=100000)):
    rows = db.scalars(
        select(IngestCredential)
        .where(IngestCredential.team_id == actor.membership.team_id)
        .order_by(IngestCredential.created_at.desc(), IngestCredential.id.desc())
        .offset(offset)
        .limit(26)
    ).all()
    now = clock(db)
    return {"items": [key_view(row, now) for row in rows[:25]], "has_more": len(rows) > 25}


@router.post("/keys", status_code=201)
def create_key(body: KeyInput, db: Database, actor: Admin, request: Request):
    # get_actor serializes mutations on the team lock, including concurrent creation
    # and role changes. Bound the number of active credentials per team.
    now = clock(db)
    count = db.scalar(
        select(func.count())
        .select_from(IngestCredential)
        .where(
            IngestCredential.team_id == actor.membership.team_id,
            IngestCredential.revoked_at.is_(None),
            IngestCredential.expires_at > now,
        )
    )
    if count >= 1000:
        raise ApiFailure(422, ErrorCode.INGEST_KEY_LIMIT)
    identifier, token = issue_key(db, body.name, team_id=actor.membership.team_id)
    row = db.get(IngestCredential, identifier)
    record(
        db,
        AuditAction.INGEST_KEY_CREATED,
        actor_id=actor.user.id,
        entity_id=identifier,
        entity_type="INGEST_CREDENTIAL",
        **audit_request(request),
    )
    result = {"key": key_view(row, now), "token": token}
    db.commit()
    return result


@router.post("/keys/{identifier}/revoke")
def revoke_key(identifier: UUID, db: Database, actor: Admin, request: Request):
    row = scoped(db, IngestCredential, identifier, actor.membership.team_id)
    now = clock(db)
    if row.revoked_at is None:
        row.revoked_at = now
        record(
            db,
            AuditAction.INGEST_KEY_REVOKED,
            actor_id=actor.user.id,
            entity_id=row.id,
            entity_type="INGEST_CREDENTIAL",
            **audit_request(request),
        )
    result = key_view(row, now)
    db.commit()
    return result
