"""Retention settings and bounded deadline recalculation."""

from datetime import timedelta

from sqlalchemy import select

from app.persistence.models import (
    Event,
    EventOccurrence,
    RawMessage,
    RetentionPolicy,
    RetentionProgress,
)
from app.workers.queue import utc

DEFAULTS = {
    "event_days": 90,
    "raw_days": 30,
    "notification_days": 90,
    "attempt_days": 30,
    "audit_days": 365,
    "task_days": 14,
    "history_days": 90,
    "health_days": 7,
}
SOURCE_FIELDS = {"event_days", "raw_days"}


def effective(db, team, source=None):
    rows = db.scalars(
        select(RetentionPolicy).where(
            RetentionPolicy.team_id == team, RetentionPolicy.scope.in_(["GLOBAL", str(source)])
        )
    ).all()
    global_policy = next((row for row in rows if row.scope == "GLOBAL"), None)
    local = next((row for row in rows if row.scope == str(source)), None)
    config = {**DEFAULTS, **(global_policy.configuration if global_policy else {})}
    if local and not local.inherit:
        config.update(
            {key: value for key, value in local.configuration.items() if key in SOURCE_FIELDS}
        )
    return config, bool(global_policy and global_policy.enabled)


def assign(db, event, raw):
    config, _ = effective(db, event.team_id, event.source_id)
    event.expires_at = utc(event.last_seen_at) + timedelta(days=config["event_days"])
    raw.expires_at = utc(raw.received_at) + timedelta(days=config["raw_days"])
    raw.source_id = event.source_id


def progress(db, team, kind):
    row = db.get(RetentionProgress, (team, kind))
    if row is None:
        row = RetentionProgress(
            team_id=team, kind=kind, revision=0, complete=False, processed=0, held=0
        )
        db.add(row)
        db.flush()
    return row


def recalculate(db, team, revision, now, limit):
    """A stable UUID cursor avoids OFFSET and unbounded UPDATE on policy changes."""
    changed = 0
    for model, kind in [(Event, "RECALCULATE_EVENTS"), (RawMessage, "RECALCULATE_RAW")]:
        state = progress(db, team, kind)
        if state.revision != revision:
            state.revision, state.cursor, state.complete, state.processed = revision, None, False, 0
        if state.complete:
            continue
        where = [model.team_id == team]
        if state.cursor:
            where.append(model.id > state.cursor)
        rows = db.scalars(
            select(model).where(*where).order_by(model.id).limit(limit).with_for_update()
        ).all()
        # Old installations have no source copied onto raw-message metadata.
        if model is RawMessage:
            sources = (
                dict(
                    db.execute(
                        select(EventOccurrence.raw_message_id, Event.source_id)
                        .join(Event, Event.id == EventOccurrence.event_id)
                        .where(EventOccurrence.raw_message_id.in_([row.id for row in rows]))
                    ).all()
                )
                if rows
                else {}
            )
            for row in rows:
                if row.source_id is None:
                    row.source_id = sources.get(row.id)
        cache = {}
        for row in rows:
            if row.source_id not in cache:
                cache[row.source_id] = effective(db, team, row.source_id)[0]
            config = cache[row.source_id]
            if model is Event:
                if not row.retiring:
                    row.expires_at = utc(row.last_seen_at) + timedelta(days=config["event_days"])
            else:
                if not row.purged_at:
                    row.expires_at = utc(row.received_at) + timedelta(days=config["raw_days"])
            changed += 1
        if rows:
            state.cursor = rows[-1].id
            state.processed += len(rows)
        state.complete, state.checked_at = len(rows) < limit, now
    return changed
