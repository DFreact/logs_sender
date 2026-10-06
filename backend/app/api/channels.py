from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.adapters.output import ADAPTER_KINDS, Adapter
from app.api.channel_schemas import AdapterInput, ChannelInput, Kind, PreviewInput, TemplateInput
from app.api.configuration import Admin, audit, scoped
from app.api.dependencies import Database
from app.api.errors import ApiFailure, ErrorCode
from app.persistence.models import (
    Notification,
    NotificationChannel,
    OutputAdapter,
    RoutingRule,
    Template,
    TemplateVersion,
    User,
    utcnow,
)
from app.security.audit import AuditAction
from app.security.channel_secrets import protect, reveal
from app.services.templates import render
from app.workers.queue import utc

router = APIRouter(prefix="/api/v1")


def adapter_state(db, team_id, kind, settings):
    row = db.scalar(
        select(OutputAdapter).where(OutputAdapter.team_id == team_id, OutputAdapter.kind == kind)
    )
    allowed = (kind != "TELEGRAM" or settings.telegram_allowed) and (
        kind != "MAX" or settings.max_allowed
    )
    enabled = (row.enabled if row else kind != "TELEGRAM") and allowed
    visible = (row.visible if row else kind != "TELEGRAM") and allowed
    channels = db.scalars(
        select(NotificationChannel).where(
            NotificationChannel.team_id == team_id, NotificationChannel.kind == kind
        )
    ).all()
    ids = {str(c.id) for c in channels}
    rules = db.scalars(select(RoutingRule).where(RoutingRule.team_id == team_id)).all()
    return {
        "kind": kind,
        "installed": True,
        "enabled": enabled,
        "visible": visible,
        "configured": bool(settings.outbound_hosts and settings.outbound_networks) and allowed,
        "health_status": "UNKNOWN",
        "version": row.version if row else 1,
        "pending_notifications": db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.team_id == team_id,
                Notification.kind == kind,
                Notification.status.in_(["PENDING", "PROCESSING", "RETRYING"]),
            )
        ),
        "affected_rules": [
            r.name for r in rules if any(a.get("target_id") in ids for a in r.actions)
        ],
    }


def channel_view(db, row, settings):
    adapter = adapter_state(db, row.team_id, row.kind, settings)
    configured = bool(row.template_id) and (
        bool(row.secret_ciphertext)
        if row.kind in ("TELEGRAM", "MAX") or row.configuration.get("username")
        else True
    )
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "enabled": row.enabled,
        "version": row.version,
        "secret_version": row.secret_version,
        "secret_configured": bool(row.secret_ciphertext),
        "configuration": row.configuration,
        "template_id": row.template_id,
        "configured": configured,
        "available": configured
        and row.enabled
        and adapter["enabled"]
        and adapter["visible"]
        and adapter["configured"],
        "health_status": row.health_status,
        "checked_at": utc(row.checked_at) if row.checked_at else None,
    }


def template_view(row):
    return {
        k: getattr(row, k) for k in ("id", "name", "kind", "version", "subject", "body", "html")
    }


def capacity(db, model, team_id):
    if db.scalar(select(func.count()).select_from(model).where(model.team_id == team_id)) >= 100:
        raise ApiFailure(422, ErrorCode.CONFIGURATION_LIMIT)


@router.get("/output-adapters")
def adapters(db: Database, actor: Admin, request: Request):
    return {
        "items": [
            adapter_state(db, actor.membership.team_id, k, request.app.state.settings)
            for k in ADAPTER_KINDS
        ],
        "total": len(ADAPTER_KINDS),
    }


@router.patch("/output-adapters/{kind}")
def update_adapter(kind: Kind, body: AdapterInput, db: Database, actor: Admin, request: Request):
    team_id = actor.membership.team_id
    settings = request.app.state.settings
    state = adapter_state(db, team_id, kind, settings)
    if body.version != state["version"]:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    if (body.enabled or body.visible) and (
        (kind == "TELEGRAM" and not settings.telegram_allowed)
        or (kind == "MAX" and not settings.max_allowed)
    ):
        raise ApiFailure(422, ErrorCode.OUTBOUND_BLOCKED)
    row = db.scalar(
        select(OutputAdapter).where(OutputAdapter.team_id == team_id, OutputAdapter.kind == kind)
    )
    if row is None:
        row = OutputAdapter(team_id=team_id, kind=kind, version=1)
        db.add(row)
    row.enabled, row.visible, row.version = body.enabled, body.visible, body.version + 1
    db.flush()
    audit(db, request, actor, AuditAction.OUTPUT_ADAPTER_UPDATED, row, "OUTPUT_ADAPTER")
    db.commit()
    return adapter_state(db, team_id, kind, settings)


@router.get("/channels")
def channels(db: Database, actor: Admin, request: Request):
    rows = db.scalars(
        select(NotificationChannel)
        .where(NotificationChannel.team_id == actor.membership.team_id)
        .order_by(NotificationChannel.name, NotificationChannel.id)
    ).all()
    return {
        "items": [channel_view(db, r, request.app.state.settings) for r in rows],
        "total": len(rows),
    }


def save_channel(body, db, actor, request, row=None):
    settings, team_id = request.app.state.settings, actor.membership.team_id
    creating, kind = row is None, body.configuration.kind
    if creating:
        capacity(db, NotificationChannel, team_id)
        adapter = adapter_state(db, team_id, kind, settings)
        if not adapter["enabled"] or not adapter["visible"]:
            raise ApiFailure(422, ErrorCode.CHANNEL_UNAVAILABLE)
    elif row.version != body.version or row.secret_version != body.secret_version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    elif row.kind != kind:
        raise ApiFailure(422, ErrorCode.VALIDATION_ERROR)
    if body.template_id:
        template = scoped(db, Template, body.template_id, team_id)
        if template.kind != kind:
            raise ApiFailure(422, ErrorCode.TEMPLATE_INVALID)
    configuration = body.configuration.model_dump()
    if creating or configuration != row.configuration:
        Adapter(settings, configuration).validate_configuration()
    if creating:
        row = NotificationChannel(
            id=uuid4(),
            team_id=team_id,
            kind=kind,
            version=1,
            secret_version=0,
            secret_ciphertext="",
        )
        db.add(row)
    values = {
        "name": body.name,
        "enabled": body.enabled,
        "template_id": body.template_id,
        "configuration": configuration,
    }
    changed = creating or any(getattr(row, k) != v for k, v in values.items())
    if changed:
        for k, v in values.items():
            setattr(row, k, v)
        if not creating:
            row.version += 1
    if body.secret is not None or body.clear_secret:
        row.secret_ciphertext = (
            ""
            if body.clear_secret
            else protect(settings, team_id, row.id, body.secret.get_secret_value())
        )
        row.secret_version += 1
        changed = True
    if changed:
        row.health_status, row.checked_at = "UNKNOWN", None
    db.flush()
    audit(
        db,
        request,
        actor,
        AuditAction.CHANNEL_CREATED if creating else AuditAction.CHANNEL_UPDATED,
        row,
        "CHANNEL",
    )
    db.commit()
    return channel_view(db, row, settings)


@router.post("/channels", status_code=201)
def create_channel(body: ChannelInput, db: Database, actor: Admin, request: Request):
    return save_channel(body, db, actor, request)


@router.patch("/channels/{identifier}")
def update_channel(
    identifier: UUID, body: ChannelInput, db: Database, actor: Admin, request: Request
):
    return save_channel(
        body,
        db,
        actor,
        request,
        scoped(db, NotificationChannel, identifier, actor.membership.team_id),
    )


@router.post("/channels/{identifier}/test-connection")
def test_connection(identifier: UUID, db: Database, actor: Admin, request: Request):
    from app.adapters.output.probe import probe

    row = scoped(db, NotificationChannel, identifier, actor.membership.team_id)
    settings = request.app.state.settings
    adapter = adapter_state(db, row.team_id, row.kind, settings)
    if not row.enabled or not adapter["enabled"]:
        raise ApiFailure(422, ErrorCode.CHANNEL_UNAVAILABLE)
    secret = reveal(settings, row.team_id, row.id, row.secret_ciphertext)
    failure = None
    try:
        result = probe(settings, row.configuration, secret)
        row.health_status = "DEGRADED" if result == "TLS_ONLY" else "HEALTHY"
    except ApiFailure as exc:
        failure, result = exc, None
        row.health_status = "UNAVAILABLE"
    row.checked_at = utcnow()
    audit(db, request, actor, AuditAction.CHANNEL_TESTED, row, "CHANNEL")
    db.commit()
    if failure:
        raise failure
    return {"result": result, "channel": channel_view(db, row, settings)}


@router.get("/templates")
def templates(db: Database, actor: Admin):
    rows = db.scalars(
        select(Template)
        .where(Template.team_id == actor.membership.team_id)
        .order_by(Template.name, Template.id)
    ).all()
    return {"items": [template_view(r) for r in rows], "total": len(rows)}


def save_template(body, db, actor, request, row=None):
    creating = row is None
    if creating:
        capacity(db, Template, actor.membership.team_id)
    elif row.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    elif row.kind != body.kind:
        raise ApiFailure(422, ErrorCode.TEMPLATE_INVALID)
    values = body.model_dump(exclude={"version"})
    render(
        values,
        {
            k: "x"
            for k in ("subject", "body", "sender", "source", "severity", "category", "event_type")
        },
    )
    if creating:
        row = Template(team_id=actor.membership.team_id, version=1, **values)
        db.add(row)
    else:
        for k, v in values.items():
            setattr(row, k, v)
        row.version += 1
    db.flush()
    db.add(
        TemplateVersion(
            template_id=row.id,
            version=row.version,
            actor_id=actor.user.id,
            snapshot={**values, "version": row.version},
        )
    )
    audit(
        db,
        request,
        actor,
        AuditAction.TEMPLATE_CREATED if creating else AuditAction.TEMPLATE_UPDATED,
        row,
        "TEMPLATE",
    )
    db.commit()
    return template_view(row)


@router.post("/templates", status_code=201)
def create_template(body: TemplateInput, db: Database, actor: Admin, request: Request):
    return save_template(body, db, actor, request)


@router.patch("/templates/{identifier}")
def update_template(
    identifier: UUID, body: TemplateInput, db: Database, actor: Admin, request: Request
):
    return save_template(
        body, db, actor, request, scoped(db, Template, identifier, actor.membership.team_id)
    )


@router.post("/templates/preview")
def preview(body: PreviewInput, actor: Admin):
    return render(body.template.model_dump(), body.event.model_dump())


@router.get("/templates/{identifier}/versions")
def versions(
    identifier: UUID,
    db: Database,
    actor: Admin,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(25, ge=1, le=100),
):
    scoped(db, Template, identifier, actor.membership.team_id)
    rows = db.execute(
        select(TemplateVersion, User.display_name)
        .join(User, User.id == TemplateVersion.actor_id)
        .where(TemplateVersion.template_id == identifier)
        .order_by(TemplateVersion.version.desc())
        .offset(offset)
        .limit(limit)
    ).all()
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
        "total": db.scalar(
            select(func.count())
            .select_from(TemplateVersion)
            .where(TemplateVersion.template_id == identifier)
        ),
    }
