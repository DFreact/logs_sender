"""Acknowledge only after an immutable blob and its durable task are committed."""

import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.api.errors import ApiFailure, ErrorCode
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    AdapterInstallation,
    IngestCredential,
    RawMessage,
    Team,
)
from app.security.tokens import digest, new_token
from app.storage.files import BlobStore
from app.storage.service import attach, reserve
from app.workers.queue import clock, enqueue, insert_for, utc


def store_for(settings):
    return BlobStore(
        settings.storage_root, settings.blob_max_bytes, settings.storage_min_free_bytes
    )


def issue_key(db, name, days=90, team_id=DEFAULT_TEAM_ID):
    token = new_token()
    row = IngestCredential(
        team_id=team_id,
        name=name,
        token_hash=digest(token),
        expires_at=clock(db) + timedelta(days=days),
    )
    db.add(row)
    db.flush()
    return row.id, token


def credential(db, token_hash, *, consume=False, limit=120, exclusive=False):
    row = db.scalar(
        select(IngestCredential)
        .where(IngestCredential.token_hash == token_hash)
        .with_for_update(read=not (consume or exclusive))
    )
    now = clock(db)
    if row is None or row.revoked_at or utc(row.expires_at) <= now:
        raise ApiFailure(401, ErrorCode.INVALID_CREDENTIALS)
    if consume:
        window = int(now.timestamp()) // 60
        if row.window_start != window:
            row.window_start, row.attempts = window, 0
        if row.attempts >= limit:
            raise ApiFailure(429, ErrorCode.RATE_LIMITED)
        row.attempts += 1
    return row


def adapter(db, kind, team_id):
    query = select(AdapterInstallation).where(
        AdapterInstallation.team_id == team_id, AdapterInstallation.kind == kind
    )
    row = db.scalar(query.with_for_update(read=True))
    if row is None:
        db.execute(
            insert_for(db, AdapterInstallation)
            .values(id=uuid4(), team_id=team_id, kind=kind, enabled=True, visible=True)
            .on_conflict_do_nothing(index_elements=["team_id", "kind"])
        )
        row = db.scalar(query.with_for_update(read=True))
    if not row.enabled:
        raise ApiFailure(503, ErrorCode.SERVICE_UNAVAILABLE)
    return row


def receive(
    factory,
    store,
    payload,
    content_type,
    *,
    kind,
    token_hash=None,
    idempotency_key=None,
    envelope_sender="",
    recipients=None,
):
    payload_hash = hashlib.sha256(content_type.encode() + b"\0" + payload).hexdigest()
    key_hash = digest(idempotency_key) if idempotency_key is not None else None
    # Authenticate before allocating disk. Recheck under lock in the final transaction.
    with factory.begin() as db:
        # The reservation's team foreign key takes a key-share lock at flush.
        # Lock Team FIRST, as configuration mutations do, to avoid the cycle
        # credential/adapter -> Team -> credential/adapter during preflight commit.
        scope = (
            select(IngestCredential.team_id)
            .where(IngestCredential.token_hash == token_hash)
            .scalar_subquery()
            if token_hash
            else DEFAULT_TEAM_ID
        )
        team_id = db.scalar(
            select(Team.id).where(Team.id == scope).with_for_update(read=True, of=Team)
        )
        if team_id is None:
            raise ApiFailure(401, ErrorCode.INVALID_CREDENTIALS)
        key = credential(db, token_hash) if token_hash else None
        if key and key.team_id != team_id:
            raise ApiFailure(401, ErrorCode.INVALID_CREDENTIALS)
        adapter(db, kind, team_id)
        if key_hash:
            existing = db.scalar(
                select(RawMessage).where(
                    RawMessage.credential_id == key.id, RawMessage.idempotency_hash == key_hash
                )
            )
            if existing:
                if existing.payload_hash != payload_hash:
                    raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
                return existing.id
        # Reservation becomes durable with the preflight checks, before filesystem I/O.
        blob_id = reserve(db, team_id)
    written = store.write(blob_id, [payload])
    with factory.begin() as db:
        db.execute(select(Team).where(Team.id == team_id).with_for_update(read=True)).scalar_one()
        key = credential(db, token_hash, exclusive=bool(key_hash)) if token_hash else None
        installation = adapter(db, kind, team_id)
        if key_hash:
            existing = db.scalar(
                select(RawMessage).where(
                    RawMessage.credential_id == key.id, RawMessage.idempotency_hash == key_hash
                )
            )
            if existing:
                if existing.payload_hash != payload_hash:
                    raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
                return existing.id  # losing staged file is collected after grace period
        identifier = uuid4()
        db.add(
            RawMessage(
                id=identifier,
                team_id=team_id,
                adapter_id=installation.id,
                credential_id=key.id if key else None,
                idempotency_hash=key_hash,
                payload_hash=payload_hash,
                blob_id=blob_id,
                content_type=content_type,
                received_at=clock(db),
                envelope_sender=envelope_sender[:320],
                recipients=recipients or [],
            )
        )
        attach(
            db,
            blob_id,
            owner_id=identifier,
            owner_kind="RAW_MESSAGE",
            team_id=team_id,
            written=written,
        )
        enqueue(
            db, "NORMALIZE", identifier, f"normalize:{identifier}", team_id=team_id, max_attempts=20
        )
    return identifier
