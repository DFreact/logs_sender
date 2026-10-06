from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from app.persistence.models import DEFAULT_TEAM_ID, AuditEntry, utcnow


class AuditAction(StrEnum):
    RETENTION_UPDATED = "RETENTION_UPDATED"
    APPEARANCE_UPDATED = "APPEARANCE_UPDATED"
    DIGEST_CREATED = "DIGEST_CREATED"
    DIGEST_UPDATED = "DIGEST_UPDATED"
    DIGEST_PARTIAL = "DIGEST_PARTIAL"
    DIGEST_WAIT = "DIGEST_WAIT"
    EVENT_ACKNOWLEDGED = "EVENT_ACKNOWLEDGED"
    EVENT_RESOLVED = "EVENT_RESOLVED"
    EVENT_SUPPRESSED = "EVENT_SUPPRESSED"
    ESCALATION_POLICY_CREATED = "ESCALATION_POLICY_CREATED"
    ESCALATION_POLICY_UPDATED = "ESCALATION_POLICY_UPDATED"
    NOTIFICATION_RETRIED = "NOTIFICATION_RETRIED"
    NOTIFICATION_CANCELLED = "NOTIFICATION_CANCELLED"
    TEST_NOTIFICATION_CREATED = "TEST_NOTIFICATION_CREATED"
    CHANNEL_CREATED = "CHANNEL_CREATED"
    CHANNEL_UPDATED = "CHANNEL_UPDATED"
    CHANNEL_TESTED = "CHANNEL_TESTED"
    OUTPUT_ADAPTER_UPDATED = "OUTPUT_ADAPTER_UPDATED"
    TEMPLATE_CREATED = "TEMPLATE_CREATED"
    TEMPLATE_UPDATED = "TEMPLATE_UPDATED"
    ROUTING_RULE_CREATED = "ROUTING_RULE_CREATED"
    ROUTING_RULE_UPDATED = "ROUTING_RULE_UPDATED"
    SOURCE_CREATED = "SOURCE_CREATED"
    SOURCE_UPDATED = "SOURCE_UPDATED"
    IDENTIFICATION_RULE_CREATED = "IDENTIFICATION_RULE_CREATED"
    IDENTIFICATION_RULE_UPDATED = "IDENTIFICATION_RULE_UPDATED"
    DEDUP_POLICY_UPDATED = "DEDUP_POLICY_UPDATED"
    INGEST_KEY_CREATED = "INGEST_KEY_CREATED"
    INGEST_KEY_REVOKED = "INGEST_KEY_REVOKED"
    LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_PASSWORD_CHANGED = "USER_PASSWORD_CHANGED"
    ADMIN_BOOTSTRAPPED = "ADMIN_BOOTSTRAPPED"


SAFE_FIELDS = frozenset(
    {
        "username",
        "display_name",
        "role",
        "active",
        "password_changed",
        "appearance_palette",
        "appearance_mode",
        "retention_enabled",
        "retention_inherit",
        "event_days",
        "raw_days",
        "notification_days",
        "attempt_days",
        "audit_days",
        "task_days",
        "history_days",
        "health_days",
    }
)


def safe_snapshot(value: dict | None) -> dict:
    return {
        key: item
        for key, item in (value or {}).items()
        if key in SAFE_FIELDS and isinstance(item, (str, bool))
    }


def record(
    db: Session,
    action: AuditAction,
    *,
    actor_id: UUID | None = None,
    entity_type: str = "USER",
    entity_id: UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    request_id: str | None = None,
    request_ip: str | None = None,
):
    db.add(
        AuditEntry(
            team_id=DEFAULT_TEAM_ID,
            actor_id=actor_id,
            action=action,
            entity_id=entity_id,
            entity_type=entity_type,
            before=safe_snapshot(before),
            after=safe_snapshot(after),
            request_ip=request_ip,
            request_id=request_id,
            expires_at=utcnow() + timedelta(days=365),
        )
    )
