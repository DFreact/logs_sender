import re
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.adapters.input.rest import validate
from app.api.dependencies import Database
from app.api.errors import ApiFailure, ErrorCode
from app.persistence.models import EventOccurrence, RawMessage, WorkItem
from app.security.tokens import TOKEN_RE, digest
from app.services.ingestion import credential, receive, store_for
from app.storage.files import StorageError

router = APIRouter(prefix="/api/v1/ingest")


def authenticate(request, factory, *, consume=True):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not TOKEN_RE.fullmatch(token):
        raise ApiFailure(401, ErrorCode.INVALID_CREDENTIALS)
    hashed = digest(token)
    with factory.begin() as db:
        row = credential(
            db, hashed, consume=consume, limit=request.app.state.settings.ingest_per_minute
        )
        identifier = row.id
    return hashed, identifier


@router.post("", status_code=202)
async def ingest(request: Request, db: Database):
    factory = request.app.state.session_factory
    hashed, _ = await run_in_threadpool(authenticate, request, factory)
    settings = request.app.state.settings
    key = request.headers.get("idempotency-key")
    if key is not None and not re.fullmatch(r"[\x21-\x7e]{1,128}", key):
        raise ApiFailure(400, ErrorCode.INVALID_INPUT)
    content_type = request.headers.get("content-type", "").lower().replace(" ", "")
    if content_type not in {
        "application/json",
        "text/plain",
        "application/json;charset=utf-8",
        "text/plain;charset=utf-8",
    }:
        raise ApiFailure(415, ErrorCode.UNSUPPORTED_CONTENT_TYPE)
    if request.headers.get("content-encoding", "identity") != "identity":
        raise ApiFailure(415, ErrorCode.UNSUPPORTED_CONTENT_TYPE)
    content_type = content_type.split(";")[0]
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > min(settings.ingest_max_bytes, settings.blob_max_bytes):
            raise ApiFailure(413, ErrorCode.REQUEST_TOO_LARGE)
        payload.extend(chunk)
    raw = bytes(payload)
    await run_in_threadpool(validate, raw, content_type)
    try:
        identifier = await run_in_threadpool(
            receive,
            factory,
            store_for(settings),
            raw,
            content_type,
            kind="REST",
            token_hash=hashed,
            idempotency_key=key,
        )
    except (OSError, StorageError):
        raise ApiFailure(503, ErrorCode.SERVICE_UNAVAILABLE) from None
    return {"receipt_id": identifier}


@router.get("/{identifier}")
def receipt(identifier: UUID, request: Request, db: Database):
    _, credential_id = authenticate(request, request.app.state.session_factory)
    raw = db.scalar(
        select(RawMessage).where(
            RawMessage.id == identifier, RawMessage.credential_id == credential_id
        )
    )
    if not raw:
        raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    event_id = db.scalar(
        select(EventOccurrence.event_id).where(EventOccurrence.raw_message_id == raw.id)
    )
    task = db.scalar(
        select(WorkItem).where(
            WorkItem.team_id == raw.team_id,
            WorkItem.kind == "NORMALIZE",
            WorkItem.entity_id == raw.id,
        )
    )
    return {"receipt_id": raw.id, "state": task.state if task else "UNKNOWN", "event_id": event_id}
