from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import load_only
from starlette.responses import Response

from app.api.cursors import decode_cursor, encode_cursor, scope_for
from app.api.dependencies import Database, require
from app.api.errors import ApiFailure, ErrorCode
from app.domain.enums import EventStatus, Severity
from app.persistence.models import (
    Attachment,
    BlobRecord,
    Event,
    EventOccurrence,
    RawMessage,
    RuleExecution,
    RuleVersion,
    Source,
)
from app.security.permissions import Permission
from app.services.ingestion import store_for
from app.storage.files import StorageError
from app.workers.queue import clock, utc

router = APIRouter(prefix="/api/v1/events")
Reader = Annotated[object, Depends(require(Permission.VIEW_EVENTS))]


_UNSET = object()


def event_item(row, db, source_name=_UNSET):
    if source_name is _UNSET:
        source_name = db.get(Source, row.source_id).name if row.source_id else None
    return {
        "id": row.id,
        "subject": row.subject,
        "sender": row.sender,
        "status": row.status,
        "severity": row.severity,
        "source_id": row.source_id,
        "source_name": source_name,
        "category": row.category,
        "event_type": row.event_type,
        "tags": row.tags,
        "first_seen_at": utc(row.first_seen_at),
        "last_seen_at": utc(row.last_seen_at),
        "input_adapter": row.input_adapter,
        "received_at": utc(row.received_at),
        "occurrence_count": row.occurrence_count,
    }


def event_for(db, actor, identifier):
    row = db.scalar(
        select(Event).where(Event.id == identifier, Event.team_id == actor.membership.team_id)
    )
    if row is None:
        raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    return row


@router.get("")
def list_events(
    db: Database,
    actor: Reader,
    request: Request,
    cursor: str | None = Query(None, max_length=1024),
    offset: int = Query(0, ge=0, le=0),
    limit: int = Query(25, ge=1, le=100),
    source_id: UUID | None = None,
    unknown_source: bool = False,
    q: str = Query("", max_length=200),
    status: EventStatus | None = None,
    severity: Severity | None = None,
    adapter: Literal["SMTP", "REST"] | None = None,
    received_from: datetime | None = None,
    received_to: datetime | None = None,
):
    conditions = [Event.team_id == actor.membership.team_id]
    if source_id:
        conditions.append(Event.source_id == source_id)
    if unknown_source:
        conditions.append(Event.source_id.is_(None))
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(
            Event.subject.ilike(pattern, escape="\\") | Event.sender.ilike(pattern, escape="\\")
        )
    for column, value in (
        (Event.status, status),
        (Event.severity, severity),
        (Event.input_adapter, adapter),
    ):
        if value:
            conditions.append(column == value)
    for value in (received_from, received_to):
        if value and value.tzinfo is None:
            raise ApiFailure(422, ErrorCode.VALIDATION_ERROR)
    if received_from:
        conditions.append(Event.received_at >= received_from)
    if received_to:
        conditions.append(Event.received_at <= received_to)
    if received_from and received_to and received_from > received_to:
        raise ApiFailure(422, ErrorCode.VALIDATION_ERROR)
    settings = request.app.state.settings
    scope = scope_for(
        actor.membership.team_id,
        [source_id, unknown_source, q, status, severity, adapter, received_from, received_to],
    )
    before, position, issued = (
        decode_cursor(settings, cursor, scope) if cursor else (clock(db), None, None)
    )
    conditions.append(Event.received_at <= before)
    if position:
        conditions.append(tuple_(Event.received_at, Event.id) < position)
    try:
        if db.bind.dialect.name == "postgresql":
            db.execute(
                select(
                    func.set_config("statement_timeout", str(settings.event_query_timeout_ms), True)
                )
            )
        rows = db.execute(event_page_query(conditions, limit + 1)).all()
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "57014":
            db.rollback()
            raise ApiFailure(422, ErrorCode.QUERY_TOO_BROAD) from None
        raise
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [event_item(row, db, source) for row, source in rows],
        "next_cursor": encode_cursor(
            settings, scope, before, (utc(rows[-1][0].received_at), rows[-1][0].id), issued
        )
        if has_more
        else None,
        "as_of": utc(before),
    }


def event_page_query(conditions, limit):
    # Exclude large bodies and fetch source labels in the same query.
    columns = (
        "id",
        "subject",
        "sender",
        "status",
        "severity",
        "source_id",
        "category",
        "event_type",
        "tags",
        "first_seen_at",
        "last_seen_at",
        "input_adapter",
        "received_at",
        "occurrence_count",
    )
    return (
        select(Event, Source.name)
        .outerjoin(Source, Source.id == Event.source_id)
        .options(load_only(*(getattr(Event, name) for name in columns)))
        .where(*conditions)
        .order_by(Event.received_at.desc(), Event.id.desc())
        .limit(limit)
    )


@router.get("/{identifier}")
def event_detail(
    identifier: UUID, db: Database, actor: Reader, occurrence_offset: int = Query(0, ge=0)
):
    row = event_for(db, actor, identifier)
    occurrences = db.execute(
        select(EventOccurrence, RawMessage)
        .join(RawMessage, RawMessage.id == EventOccurrence.raw_message_id)
        .where(EventOccurrence.event_id == row.id)
        .order_by(EventOccurrence.received_at.desc(), EventOccurrence.id.desc())
        .offset(occurrence_offset)
        .limit(25)
    ).all()
    history = []
    for occurrence, raw in occurrences:
        attachments = db.execute(
            select(Attachment, BlobRecord)
            .join(BlobRecord, BlobRecord.id == Attachment.blob_id)
            .where(Attachment.raw_message_id == raw.id)
        ).all()
        version = (
            db.get(RuleVersion, occurrence.rule_version_id) if occurrence.rule_version_id else None
        )
        history.append(
            {
                "id": occurrence.id,
                "snapshot": occurrence.normalized_snapshot,
                "rule_name": version.snapshot["name"] if version else None,
                "rule_version": version.version if version else None,
                "raw_message_id": raw.id,
                "raw_available": raw.purged_at is None,
                "received_at": utc(occurrence.received_at),
                "limited": raw.state == "LIMITED",
                "envelope_sender": raw.envelope_sender,
                "recipients": raw.recipients,
                "attachments": [
                    {"id": a.id, "filename": a.filename, "size": b.size} for a, b in attachments
                ],
            }
        )
    return {**event_item(row, db), "body": row.body, "occurrences": history}


@router.get("/{identifier}/raw/{raw_id}")
def raw_download(identifier: UUID, raw_id: UUID, request: Request, db: Database, actor: Reader):
    event_for(db, actor, identifier)
    raw = db.scalar(
        select(RawMessage)
        .join(EventOccurrence, EventOccurrence.raw_message_id == RawMessage.id)
        .where(
            EventOccurrence.event_id == identifier,
            RawMessage.id == raw_id,
            RawMessage.team_id == actor.membership.team_id,
        )
    )
    if not raw:
        raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    if raw.purged_at or raw.blob_id is None:
        raise ApiFailure(410, ErrorCode.RAW_MESSAGE_EXPIRED)
    extension = (
        "eml"
        if raw.content_type == "message/rfc822"
        else "json"
        if raw.content_type == "application/json"
        else "txt"
    )
    return download(db, request, raw.blob_id, f"message.{extension}")


@router.get("/{identifier}/processing")
def processing_history(
    identifier: UUID, db: Database, actor: Reader, offset: int = Query(0, ge=0, le=1000000)
):
    event_for(db, actor, identifier)
    rows = db.scalars(
        select(RuleExecution)
        .where(RuleExecution.event_id == identifier)
        .order_by(RuleExecution.created_at.desc(), RuleExecution.id.desc())
        .offset(offset)
        .limit(25)
    ).all()
    return {
        "items": [
            {"id": row.id, "created_at": utc(row.created_at), "decision": row.result}
            for row in rows
        ],
        "total": db.scalar(
            select(func.count())
            .select_from(RuleExecution)
            .where(RuleExecution.event_id == identifier)
        ),
    }


@router.get("/{identifier}/attachments/{attachment_id}")
def attachment_download(
    identifier: UUID, attachment_id: UUID, request: Request, db: Database, actor: Reader
):
    event_for(db, actor, identifier)
    item = db.scalar(
        select(Attachment)
        .join(EventOccurrence, EventOccurrence.raw_message_id == Attachment.raw_message_id)
        .where(EventOccurrence.event_id == identifier, Attachment.id == attachment_id)
    )
    if not item:
        raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    # Fixed filename avoids CRLF/path tricks and dangerous executable extensions.
    return download(db, request, item.blob_id, "attachment.bin")


def download(db, request, blob_id, filename):
    blob = db.scalar(select(BlobRecord).where(BlobRecord.id == blob_id).with_for_update(read=True))
    if blob is None or blob.state != "READY":
        raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    try:
        content = store_for(request.app.state.settings).read(
            blob.id, size=blob.size, checksum=blob.checksum
        )
    except (OSError, ValueError, StorageError):
        raise ApiFailure(503, ErrorCode.SERVICE_UNAVAILABLE) from None
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
