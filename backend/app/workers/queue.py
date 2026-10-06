"""Durable task state. Callers own transactions; Redis contains only wake-ups."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.persistence.models import DEFAULT_TEAM_ID, WorkItem, utcnow


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def clock(db: Session) -> datetime:
    return (
        db.scalar(select(func.clock_timestamp()))
        if db.bind.dialect.name == "postgresql"
        else utcnow()
    )


def insert_for(db: Session, model):
    return pg_insert(model) if db.bind.dialect.name == "postgresql" else sqlite_insert(model)


def enqueue(
    db: Session,
    kind: str,
    entity_id: UUID,
    dedupe_key: str,
    *,
    team_id: UUID = DEFAULT_TEAM_ID,
    due_at: datetime | None = None,
    max_attempts: int = 5,
) -> UUID:
    if not kind or len(kind) > 40 or not dedupe_key or len(dedupe_key) > 180 or max_attempts < 1:
        raise ValueError("INVALID_WORK_ITEM")
    now = clock(db)
    statement = (
        insert_for(db, WorkItem)
        .values(
            id=uuid4(),
            team_id=team_id,
            kind=kind,
            entity_id=entity_id,
            dedupe_key=dedupe_key,
            state="PENDING",
            due_at=due_at or now,
            created_at=now,
            attempts=0,
            max_attempts=max_attempts,
        )
        .on_conflict_do_nothing(index_elements=[WorkItem.team_id, WorkItem.dedupe_key])
    )
    db.execute(statement)
    item = db.scalar(
        select(WorkItem).where(WorkItem.team_id == team_id, WorkItem.dedupe_key == dedupe_key)
    )
    if item.kind != kind or item.entity_id != entity_id:
        raise ValueError("WORK_DEDUPE_CONFLICT")
    return item.id


@dataclass(frozen=True)
class Lease:
    id: UUID
    token: UUID
    team_id: UUID
    kind: str
    entity_id: UUID


def claim(db: Session, item_id: UUID, seconds: int, *, now: datetime | None = None) -> Lease | None:
    item = db.scalar(select(WorkItem).where(WorkItem.id == item_id).with_for_update())
    now = now or clock(db)
    if item is None or item.state != "PENDING" or utc(item.due_at) > now:
        return None
    if item.attempts >= item.max_attempts:
        item.state, item.failure_code, item.completed_at = "FAILED", "ATTEMPTS_EXHAUSTED", now
        return None
    item.state, item.lease_token = "RUNNING", uuid4()
    item.lease_until = now + timedelta(seconds=seconds)
    item.attempts += 1
    item.publication_at = item.publication_token = None
    return Lease(item.id, item.lease_token, item.team_id, item.kind, item.entity_id)


def owned(db: Session, lease: Lease, now: datetime | None = None) -> WorkItem | None:
    item = db.scalar(
        select(WorkItem)
        .where(
            WorkItem.id == lease.id,
            WorkItem.lease_token == lease.token,
            WorkItem.state == "RUNNING",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return item if item and utc(item.lease_until) > (now or clock(db)) else None


def renew(db: Session, lease: Lease, seconds: int, *, now: datetime | None = None) -> bool:
    item = owned(db, lease, now)
    if not item:
        return False
    item.lease_until = (now or clock(db)) + timedelta(seconds=seconds)
    return True


class LostLease(Exception):
    pass


def complete(
    db: Session,
    lease: Lease,
    effect: Callable[[Session], None] | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    item = owned(db, lease, now)
    if not item:
        return False
    if effect:
        effect(db)
        db.flush()
    finished = now or clock(db)
    if utc(item.lease_until) <= finished:
        # Roll back the business effect as well; caller must not catch inside its transaction.
        raise LostLease()
    item.state, item.completed_at = "SUCCEEDED", finished
    item.lease_token = item.lease_until = item.failure_code = None
    return True


def fail(
    db: Session, lease: Lease, code: str, *, permanent: bool = False, now: datetime | None = None
) -> bool:
    item = owned(db, lease, now)
    if not item:
        return False
    now = now or clock(db)
    item.failure_code = (
        code
        if code in {"TASK_FAILED", "UNKNOWN_TASK", "BLOB_INVALID", "LEASE_EXPIRED"}
        else "TASK_FAILED"
    )
    item.lease_token = item.lease_until = item.publication_at = item.publication_token = None
    if permanent or item.attempts >= item.max_attempts:
        item.state, item.completed_at = "FAILED", now
    else:
        item.state = "PENDING"
        item.due_at = now + timedelta(seconds=min(300, 2 ** min(item.attempts, 8)))
    return True


def recover_expired(db: Session, limit: int, *, now: datetime | None = None) -> int:
    now = now or clock(db)
    items = db.scalars(
        select(WorkItem)
        .where(
            WorkItem.state == "RUNNING",
            WorkItem.lease_until <= now,
        )
        .order_by(WorkItem.lease_until, WorkItem.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for item in items:
        item.lease_token = item.lease_until = item.publication_at = item.publication_token = None
        item.failure_code = "LEASE_EXPIRED"
        item.state = "FAILED" if item.attempts >= item.max_attempts else "PENDING"
        item.completed_at = now if item.state == "FAILED" else None
        item.due_at = now
    return len(items)


def reserve_publications(db: Session, seconds: int, limit: int, *, now: datetime | None = None):
    now = now or clock(db)
    items = db.scalars(
        select(WorkItem)
        .where(
            WorkItem.state == "PENDING",
            WorkItem.due_at <= now,
            or_(
                WorkItem.publication_at.is_(None),
                WorkItem.publication_at <= now - timedelta(seconds=seconds),
            ),
        )
        .order_by(WorkItem.due_at, WorkItem.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    result = []
    for item in items:
        item.publication_at, item.publication_token = now, uuid4()
        result.append((item.id, item.publication_token))
    return result


def release_publication(db: Session, item_id: UUID, token: UUID):
    db.execute(
        update(WorkItem)
        .where(
            WorkItem.id == item_id,
            WorkItem.publication_token == token,
            WorkItem.state == "PENDING",
        )
        .values(publication_at=None, publication_token=None)
    )
