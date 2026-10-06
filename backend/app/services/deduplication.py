import hashlib
import json
import unicodedata
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.persistence.models import DedupKey, DedupPolicy, Event
from app.workers.queue import insert_for, utc


def effective_policy(db, team_id, source_id):
    rows = db.scalars(
        select(DedupPolicy).where(
            DedupPolicy.team_id == team_id, DedupPolicy.scope.in_(["GLOBAL", str(source_id)])
        )
    ).all()
    specific = next((row for row in rows if row.scope == str(source_id)), None)
    return (
        specific
        if specific and not specific.inherit
        else next((row for row in rows if row.scope == "GLOBAL"), None)
    )


def canonical_value(value):
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    if isinstance(value, list):
        return sorted(canonical_value(item) for item in value)
    return value


def comparison_key(db, raw, source_id, data, limited):
    policy = effective_policy(db, raw.team_id, source_id)
    source_policy = db.scalar(
        select(DedupPolicy).where(
            DedupPolicy.team_id == raw.team_id, DedupPolicy.scope == str(source_id)
        )
    )
    if policy and policy.enabled:
        compared = {field: canonical_value(data.get(field)) for field in policy.fields}
        compared["source_policy_version"] = source_policy.version if source_policy else 0
        compared["source_id"] = str(source_id) if source_id else None
        if source_id is None:
            compared["input_adapter"] = data["adapter"]
            compared["input_stream"] = (
                str(raw.credential_id) if raw.credential_id else raw.envelope_sender
            )
        if limited and "body" in policy.fields:
            compared["limited_payload"] = raw.payload_hash
        canonical = json.dumps(compared, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        return policy, fingerprint, canonical
    return policy, None, None


def candidate_query(team_id, key_id, timestamp, window_seconds):
    window = timedelta(seconds=window_seconds)
    return (
        select(Event)
        .where(
            Event.team_id == team_id,
            Event.retiring.is_(False),
            Event.dedup_key_id == key_id,
            Event.first_seen_at > timestamp - window,
            Event.last_seen_at < timestamp + window,
        )
        .order_by(Event.first_seen_at, Event.id)
        .limit(1)
    )


def preview_occurrence(db, raw, source_id, data, limited=False):
    policy, fingerprint, canonical = comparison_key(db, raw, source_id, data, limited)
    if not policy or not policy.enabled:
        return "DISABLED", None
    if source_id is None and data["adapter"] == "REST" and raw.credential_id is None:
        return "UNKNOWN_STREAM", None
    key = db.scalar(
        select(DedupKey).where(
            DedupKey.team_id == raw.team_id,
            DedupKey.policy_id == policy.id,
            DedupKey.policy_version == policy.version,
            DedupKey.fingerprint == fingerprint,
        )
    )
    event = None
    if key and key.canonical == canonical:
        event = db.scalar(
            candidate_query(raw.team_id, key.id, utc(raw.received_at), policy.window_seconds)
        )
    return ("REPEAT" if event else "NEW"), event


def event_for_occurrence(db, raw, source_id, data, limited):
    policy, fingerprint, canonical = comparison_key(db, raw, source_id, data, limited)
    key = None
    timestamp = utc(raw.received_at)
    if fingerprint is not None:
        db.execute(
            insert_for(db, DedupKey)
            .values(
                id=uuid4(),
                team_id=raw.team_id,
                policy_id=policy.id,
                policy_version=policy.version,
                fingerprint=fingerprint,
                canonical=canonical,
            )
            .on_conflict_do_nothing(
                index_elements=["team_id", "policy_id", "policy_version", "fingerprint"]
            )
        )
        key = db.scalar(
            select(DedupKey)
            .where(
                DedupKey.team_id == raw.team_id,
                DedupKey.policy_id == policy.id,
                DedupKey.policy_version == policy.version,
                DedupKey.fingerprint == fingerprint,
            )
            .with_for_update()
        )
        if key.canonical != canonical:
            key = None  # never merge solely because a digest collides
        if key:
            event = db.scalar(
                candidate_query(
                    raw.team_id, key.id, timestamp, policy.window_seconds
                ).with_for_update()
            )
            if event:
                event.occurrence_count += 1
                event.first_seen_at = min(utc(event.first_seen_at), timestamp)
                event.last_seen_at = max(utc(event.last_seen_at), timestamp)
                # Existing status and initial content stay intact.
                return event
    event = Event(
        team_id=raw.team_id,
        source_id=source_id,
        dedup_key_id=key.id if key else None,
        input_adapter=data["adapter"],
        sender=data["sender"],
        subject=data["subject"],
        body=data["body"],
        severity=data.get("severity"),
        category=data.get("category", ""),
        event_type=data.get("event_type", ""),
        tags=data.get("tags", []),
        received_at=raw.received_at,
        first_seen_at=raw.received_at,
        last_seen_at=raw.received_at,
    )
    db.add(event)
    db.flush()
    return event
