from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.persistence.models import DEFAULT_TEAM_ID, BlobRecord, StorageScan
from app.storage.files import BlobStore, StorageError
from app.workers.queue import clock, enqueue, utc


def reserve(db, team_id=DEFAULT_TEAM_ID) -> UUID:
    """Commit the caller transaction before writing any bytes for this identifier."""
    identifier = uuid4()
    db.add(BlobRecord(id=identifier, team_id=team_id, state="STAGING"))
    return identifier


def stage(factory, store: BlobStore, chunks, team_id=DEFAULT_TEAM_ID) -> UUID:
    # This reservation is committed BEFORE any file can appear. A failed business
    # transaction leaves a recoverable STAGING object, never a referenced orphan.
    with factory.begin() as db:
        identifier = reserve(db, team_id)
    size, checksum = store.write(identifier, chunks)
    with factory.begin() as db:
        record = db.scalar(select(BlobRecord).where(BlobRecord.id == identifier).with_for_update())
        if record.state != "STAGING":
            raise StorageError("BLOB_RESERVATION_EXPIRED")
        record.size, record.checksum = size, checksum
    return identifier


def attach(
    db,
    identifier: UUID,
    owner_id: UUID,
    owner_kind: str,
    team_id=DEFAULT_TEAM_ID,
    *,
    written: tuple[int, str] | None = None,
):
    """Attach atomically with the owner. `written` must come from successful store.write.

    The optional receipt avoids an intermediate metadata transaction. The locked
    reservation must still be STAGING; a collector winning the race is never undone.
    """
    record = db.scalar(
        select(BlobRecord)
        .where(
            BlobRecord.id == identifier,
            BlobRecord.team_id == team_id,
        )
        .with_for_update()
    )
    if not record or record.state != "STAGING":
        raise StorageError("BLOB_NOT_READY")
    if not owner_kind or len(owner_kind) > 40:
        raise StorageError("INVALID_BLOB_OWNER")
    if written is not None:
        record.size, record.checksum = written
    if record.checksum is None or record.size is None:
        raise StorageError("BLOB_NOT_READY")
    record.state, record.owner_id, record.owner_kind = "READY", owner_id, owner_kind


def request_delete(db, record: BlobRecord):
    """Caller locks the row and removes its business reference in this transaction."""
    if record.state == "DELETED":
        return
    record.state, record.owner_id, record.owner_kind = "DELETING", None, None
    enqueue(
        db,
        "BLOB_DELETE",
        record.id,
        f"blob-delete:{record.id}",
        team_id=record.team_id,
        max_attempts=20,
    )


def delete_effect(factory, store, lease):
    from app.workers.queue import complete, owned

    with factory.begin() as db:
        if not owned(db, lease):
            return
        record = db.get(BlobRecord, lease.entity_id)
        if (
            not record
            or record.team_id != lease.team_id
            or record.state not in {"DELETING", "DELETED"}
        ):
            raise StorageError("BLOB_INVALID")
    # The immutable identifier can never return to READY; repeated deletion is safe.
    # No database transaction remains open during filesystem I/O.
    store.delete(lease.entity_id)
    with factory.begin() as db:

        def mark_deleted(session):
            record = session.get(BlobRecord, lease.entity_id)
            record.state, record.deleted_at = "DELETED", clock(session)

        complete(db, lease, mark_deleted)


def collect(factory, store, settings):
    with factory.begin() as db:
        now = clock(db)
        records = db.scalars(
            select(BlobRecord)
            .where(
                BlobRecord.state == "STAGING",
                BlobRecord.created_at <= now - timedelta(seconds=settings.orphan_grace_seconds),
            )
            .order_by(BlobRecord.created_at, BlobRecord.id)
            .limit(settings.work_batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        for record in records:
            request_delete(db, record)

    # Durable cursor: old orphan files left by interrupted writes/restores are
    # eventually revisited even when a process restarts. READY objects are protected.
    with factory.begin() as db:
        scan = db.scalar(select(StorageScan).where(StorageScan.id == 1).with_for_update())
        if scan is None:
            raise StorageError("STORAGE_SCAN_NOT_INITIALIZED")
        shard, cursor = scan.shard, scan.cursor
    candidates = store.scan(shard, cursor, settings.work_batch_size)
    with factory.begin() as db:
        scan = db.scalar(select(StorageScan).where(StorageScan.id == 1).with_for_update())
        if (scan.shard, scan.cursor) != (shard, cursor):
            return  # Another collector already advanced this batch.
        cutoff = clock(db) - timedelta(seconds=settings.orphan_grace_seconds)
        for _, identifier, mtime in candidates:
            if mtime > cutoff.timestamp():
                continue
            record = db.scalar(
                select(BlobRecord).where(BlobRecord.id == identifier).with_for_update()
            )
            if record is None:
                record = BlobRecord(id=identifier, team_id=DEFAULT_TEAM_ID, state="DELETING")
                db.add(record)
                db.flush()
                request_delete(db, record)
            elif record.state == "STAGING" and utc(record.created_at) <= cutoff:
                request_delete(db, record)
            elif record.state == "DELETED":
                # A writer suspended before cleanup might leave a late file. Use a
                # new unique job, keeping the immutable tombstone until it is gone.
                record.state = "DELETING"
                enqueue(
                    db,
                    "BLOB_DELETE",
                    identifier,
                    f"blob-late-delete:{identifier}:{uuid4()}",
                    team_id=record.team_id,
                    max_attempts=20,
                )
        if len(candidates) == settings.work_batch_size:
            scan.cursor = candidates[-1][0]
        else:
            scan.shard, scan.cursor = (shard + 1) % 256, ""
