from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.database import Base

DEFAULT_TEAM_ID = UUID("00000000-0000-0000-0000-000000000001")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    appearance_palette: Mapped[str] = mapped_column(
        String(16), default="blue", server_default="blue"
    )
    appearance_mode: Mapped[str] = mapped_column(String(8), default="light", server_default="light")
    appearance_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __table_args__ = (
        CheckConstraint(
            "appearance_palette IN ('blue','teal','green','violet','wine','slate')",
            name="ck_team_palette",
        ),
        CheckConstraint("appearance_mode IN ('light','dark')", name="ck_team_mode"),
        CheckConstraint("appearance_version > 0", name="ck_team_appearance_version"),
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        CheckConstraint("role IN ('ADMINISTRATOR','OPERATOR','VIEWER')", name="ck_membership_role"),
        Index("ix_memberships_team_role", "team_id", "role"),
    )


class AuthSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    team_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "team_id"],
            ["memberships.user_id", "memberships.team_id"],
        ),
        Index("ix_sessions_user", "user_id"),
    )


class LoginLimit(Base):
    __tablename__ = "login_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    __table_args__ = (Index("ix_login_limits_window_start", "window_start"),)


class AuditEntry(Base):
    __tablename__ = "audit_entries"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    entity_type: Mapped[str] = mapped_column(String(30), default="USER")
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    request_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (Index("ix_audit_team_timestamp", "team_id", "timestamp", "id"),)


class WorkItem(Base):
    __tablename__ = "work_items"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    kind: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    dedupe_key: Mapped[str] = mapped_column(String(180))
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    publication_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_token: Mapped[UUID | None] = mapped_column(Uuid)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (
        UniqueConstraint("team_id", "dedupe_key", name="uq_work_team_dedupe"),
        CheckConstraint(
            "state IN ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="ck_work_state"
        ),
        CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_work_attempts"),
        Index("ix_work_due", "state", "due_at"),
        Index("ix_work_lease", "state", "lease_until"),
    )


class BlobRecord(Base):
    __tablename__ = "blobs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    state: Mapped[str] = mapped_column(String(16), default="STAGING")
    checksum: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int | None] = mapped_column(Integer)
    owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    owner_kind: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("state IN ('STAGING','READY','DELETING','DELETED')", name="ck_blob_state"),
        CheckConstraint("size IS NULL OR size >= 0", name="ck_blob_size"),
        Index("ix_blob_state_created", "state", "created_at"),
    )


class ComponentHeartbeat(Base):
    __tablename__ = "component_heartbeats"
    component: Mapped[str] = mapped_column(String(24), primary_key=True)
    instance_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(32))


class StorageScan(Base):
    __tablename__ = "storage_scans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shard: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[str] = mapped_column(String(40), default="")


class AdapterInstallation(Base):
    __tablename__ = "adapter_installations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    kind: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("team_id", "kind", name="uq_adapter_team_kind"),
        CheckConstraint("kind IN ('SMTP','REST')", name="ck_input_kind"),
    )


class IngestCredential(Base):
    __tablename__ = "ingest_credentials"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class RawMessage(Base):
    __tablename__ = "raw_messages"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    adapter_id: Mapped[UUID] = mapped_column(ForeignKey("adapter_installations.id"))
    credential_id: Mapped[UUID | None] = mapped_column(ForeignKey("ingest_credentials.id"))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    blob_id: Mapped[UUID | None] = mapped_column(ForeignKey("blobs.id"), unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    envelope_sender: Mapped[str] = mapped_column(String(320), default="")
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    headers: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sources.id", name="fk_raw_retention_source")
    )
    __table_args__ = (
        UniqueConstraint("credential_id", "idempotency_hash", name="uq_ingest_idempotency"),
        CheckConstraint("state IN ('PENDING','NORMALIZED','LIMITED')", name="ck_raw_state"),
        Index("ix_raw_team_received", "team_id", "received_at"),
        Index("ix_raw_pending_window", "team_id", "state", "received_at"),
        Index(
            "ix_raw_expiry",
            "team_id",
            "expires_at",
            "id",
            postgresql_where=text("purged_at IS NULL"),
            sqlite_where=text("purged_at IS NULL"),
        ),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id"))
    dedup_key_id: Mapped[UUID | None] = mapped_column(ForeignKey("dedup_keys.id"), index=True)
    category: Mapped[str] = mapped_column(String(120), default="")
    event_type: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    input_adapter: Mapped[str] = mapped_column(String(16))
    sender: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="NEW")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retiring: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    acknowledged_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_event_ack_user")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_changed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_event_state_user")
    )
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('NEW','ACKNOWLEDGED','RESOLVED','SUPPRESSED')", name="ck_event_status"
        ),
        CheckConstraint(
            "severity IS NULL OR severity IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')",
            name="ck_event_severity",
        ),
        Index("ix_event_team_received", "team_id", "received_at", "id"),
        Index("ix_event_expiry", "team_id", "expires_at", "id"),
        *(
            Index(f"ix_event_{field}_received", "team_id", field, "received_at", "id")
            for field in ("source_id", "status", "severity", "input_adapter")
        ),
        *(
            Index(
                f"ix_event_{field}_search",
                field,
                postgresql_using="gin",
                postgresql_ops={field: "public.gin_trgm_ops"},
                info={"postgresql_only": True},
            ).ddl_if(dialect="postgresql")
            for field in ("subject", "sender")
        ),
    )


class EventOccurrence(Base):
    __tablename__ = "event_occurrences"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    raw_message_id: Mapped[UUID] = mapped_column(ForeignKey("raw_messages.id"), unique=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    normalized_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    rule_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("rule_versions.id"))
    __table_args__ = (Index("ix_occurrence_event_received", "event_id", "received_at"),)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    raw_message_id: Mapped[UUID] = mapped_column(ForeignKey("raw_messages.id"), index=True)
    blob_id: Mapped[UUID] = mapped_column(ForeignKey("blobs.id"), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(120))


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_source_name"),)


class IdentificationRule(Base):
    __tablename__ = "identification_rules"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    name: Mapped[str] = mapped_column(String(120))
    priority: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    conditions: Mapped[dict] = mapped_column(JSON)
    assignments: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_identification_priority", "team_id", "priority", "id"),)


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    rule_id: Mapped[UUID | None] = mapped_column(ForeignKey("identification_rules.id"))
    routing_rule_id: Mapped[UUID | None] = mapped_column(ForeignKey("routing_rules.id"))
    version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("rule_id", "version", name="uq_rule_version"),
        UniqueConstraint("routing_rule_id", "version", name="uq_routing_version"),
        CheckConstraint(
            "(rule_id IS NOT NULL AND routing_rule_id IS NULL) OR "
            "(rule_id IS NULL AND routing_rule_id IS NOT NULL)",
            name="ck_version_owner",
        ),
    )


class RoutingRule(Base):
    __tablename__ = "routing_rules"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    conditions: Mapped[dict] = mapped_column(JSON)
    actions: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_routing_priority", "team_id", "priority", "id"),)


class RuleExecution(Base):
    __tablename__ = "rule_executions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    occurrence_id: Mapped[UUID] = mapped_column(ForeignKey("event_occurrences.id"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("rule_versions.id"))
    action_index: Mapped[int] = mapped_column(Integer)
    scope_key: Mapped[UUID] = mapped_column(Uuid)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint(
            "event_id", "version_id", "action_index", "scope_key", name="uq_rule_effect"
        ),
        Index("ix_execution_occurrence", "occurrence_id", "id"),
    )


class DedupPolicy(Base):
    __tablename__ = "dedup_policies"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    scope: Mapped[str] = mapped_column(String(36))
    inherit: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    window_seconds: Mapped[int] = mapped_column(Integer, default=600)
    fields: Mapped[list] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("team_id", "scope", name="uq_dedup_scope"),
        CheckConstraint("window_seconds BETWEEN 1 AND 86400", name="ck_dedup_window"),
    )


class DedupKey(Base):
    __tablename__ = "dedup_keys"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("dedup_policies.id"))
    policy_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    canonical: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint(
            "team_id", "policy_id", "policy_version", "fingerprint", name="uq_dedup_fingerprint"
        ),
    )


class OutputAdapter(Base):
    __tablename__ = "output_adapters"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    kind: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("team_id", "kind", name="uq_output_adapter"),
        CheckConstraint("kind IN ('SMTP','TELEGRAM','MAX','WEBHOOK')", name="ck_output_kind"),
    )


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text)
    html: Mapped[str] = mapped_column(Text, default="")


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(ForeignKey("templates.id"))
    version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_version"),)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    secret_version: Mapped[int] = mapped_column(Integer, default=0)
    secret_ciphertext: Mapped[str] = mapped_column(Text, default="")
    configuration: Mapped[dict] = mapped_column(JSON)
    template_id: Mapped[UUID | None] = mapped_column(ForeignKey("templates.id"))
    health_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_channel_team", "team_id", "id"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"))
    escalation_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("escalation_runs.id", name="fk_notification_escalation"), index=True
    )
    execution_id: Mapped[UUID | None] = mapped_column(ForeignKey("rule_executions.id"), unique=True)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("notification_channels.id"))
    channel_name: Mapped[str] = mapped_column(String(120))
    channel_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    configuration: Mapped[dict] = mapped_column(JSON)
    template_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("template_versions.id"))
    context: Mapped[dict] = mapped_column(JSON)
    prepared: Mapped[dict | None] = mapped_column(JSON)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_delays: Mapped[list] = mapped_column(JSON)
    jitter_percent: Mapped[int] = mapped_column(Integer)
    failure_code: Mapped[str | None] = mapped_column(String(40))
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    work_id: Mapped[UUID | None] = mapped_column(ForeignKey("work_items.id"))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','SENT','FAILED','RETRYING','CANCELLED')",
            name="ck_notification_status",
        ),
        CheckConstraint("generation > 0 AND attempt_count >= 0", name="ck_notification_attempts"),
        Index("ix_notification_team_created", "team_id", "created_at", "id"),
        Index("ix_notification_due", "status", "due_at"),
        Index("ix_notification_lease", "status", "lease_until"),
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    notification_id: Mapped[UUID] = mapped_column(ForeignKey("notifications.id"))
    generation: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    lease_token: Mapped[UUID] = mapped_column(Uuid)
    secret_version: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "generation", "attempt_number", name="uq_delivery_attempt"
        ),
        CheckConstraint(
            "status IN ('PROCESSING','SENT','FAILED','UNKNOWN')", name="ck_delivery_status"
        ),
        Index("ix_delivery_notification_started", "notification_id", "started_at", "id"),
    )


class NotificationOperation(Base):
    __tablename__ = "notification_operations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    operation_key: Mapped[UUID] = mapped_column(Uuid)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(64))
    notification_id: Mapped[UUID] = mapped_column(ForeignKey("notifications.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("team_id", "operation_key", name="uq_notification_operation"),
    )


class EscalationPolicy(Base):
    __tablename__ = "escalation_policies"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    steps: Mapped[list] = mapped_column(JSON)


class EscalationPolicyVersion(Base):
    __tablename__ = "escalation_policy_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("escalation_policies.id"))
    version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_escalation_version"),)


class EscalationRun(Base):
    __tablename__ = "escalation_runs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("rule_executions.id"), unique=True)
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("escalation_policies.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(String(16))
    __table_args__ = (
        CheckConstraint(
            "state IN ('ACTIVE','COMPLETED','STOPPED')", name="ck_escalation_run_state"
        ),
    )


class EscalationStep(Base):
    __tablename__ = "escalation_steps"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("escalation_runs.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("notification_channels.id"))
    channel_name: Mapped[str] = mapped_column(String(120))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), default="WAITING")
    notification_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("notifications.id"), unique=True
    )
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_escalation_step"),
        CheckConstraint(
            "state IN ('WAITING','QUEUED','CANCELLED')", name="ck_escalation_step_state"
        ),
        Index("ix_escalation_step_due", "state", "due_at"),
    )


class EventAction(Base):
    __tablename__ = "event_actions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    operation_key: Mapped[UUID] = mapped_column(Uuid)
    request_version: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("team_id", "operation_key", name="uq_event_action"),)


class DigestDefinition(Base):
    __tablename__ = "digest_definitions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    configuration: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_snapshot: Mapped[dict] = mapped_column(JSON)


class DigestVersion(Base):
    __tablename__ = "digest_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    definition_id: Mapped[UUID] = mapped_column(ForeignKey("digest_definitions.id"))
    version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("definition_id", "version", name="uq_digest_version"),)


class DigestReceipt(Base):
    __tablename__ = "digest_receipts"
    occurrence_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_occurrences.id"), primary_key=True
    )
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_digest_receipt_window", "team_id", "received_at", "occurrence_id"),)


class DigestRun(Base):
    __tablename__ = "digest_runs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"))
    definition_id: Mapped[UUID] = mapped_column(ForeignKey("digest_definitions.id"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="WAITING")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    operation_actor: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[UUID | None] = mapped_column(Uuid)
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    notification_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("notifications.id"), unique=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    operation_key: Mapped[UUID | None] = mapped_column(Uuid)
    operation_version: Mapped[int | None] = mapped_column(Integer)
    operation_action: Mapped[str | None] = mapped_column(String(16))
    __table_args__ = (
        UniqueConstraint("definition_id", "window_start", "window_end", name="uq_digest_window"),
        CheckConstraint(
            "state IN ('WAITING','ATTENTION','BUILDING','READY','EMPTY','SKIPPED')",
            name="ck_digest_state",
        ),
        Index("ix_digest_run_state", "state", "window_end"),
    )


class DigestItem(Base):
    __tablename__ = "digest_items"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("digest_runs.id"), primary_key=True)
    occurrence_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_occurrences.id"), primary_key=True
    )
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(16))


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(36), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    inherit: Mapped[bool] = mapped_column(Boolean, default=True)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class RetentionProgress(Base):
    __tablename__ = "retention_progress"
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    cursor: Mapped[UUID | None] = mapped_column(Uuid)
    cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed: Mapped[int] = mapped_column(BigInteger, default=0)
    held: Mapped[int] = mapped_column(BigInteger, default=0)


class MetricCounter(Base):
    __tablename__ = "metric_counters"
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    bucket: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)


class HealthSample(Base):
    __tablename__ = "health_samples"
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    slot: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_health_sample_time", "team_id", "measured_at"),)


# Keep create_all-based test databases consistent with migrated installations.
from app.persistence.counter_schema_v1 import after_create  # noqa: E402

event.listen(Base.metadata, "after_create", after_create)

# Supporting indexes match migration 0011.
Index(
    "ix_retention_rule_version_created",
    Base.metadata.tables["rule_versions"].c["created_at"],
    Base.metadata.tables["rule_versions"].c["id"],
)
Index(
    "ix_retention_template_version_created",
    Base.metadata.tables["template_versions"].c["created_at"],
    Base.metadata.tables["template_versions"].c["id"],
)
Index(
    "ix_retention_escalation_version_created",
    Base.metadata.tables["escalation_policy_versions"].c["created_at"],
    Base.metadata.tables["escalation_policy_versions"].c["id"],
)
Index(
    "ix_retention_digest_version_created",
    Base.metadata.tables["digest_versions"].c["created_at"],
    Base.metadata.tables["digest_versions"].c["id"],
)
Index(
    "ix_retention_occurrence_rule_version",
    Base.metadata.tables["event_occurrences"].c["rule_version_id"],
)
Index("ix_retention_execution_version", Base.metadata.tables["rule_executions"].c["version_id"])
Index(
    "ix_retention_notification_template",
    Base.metadata.tables["notifications"].c["template_version_id"],
)
Index(
    "ix_retention_digest_finished",
    Base.metadata.tables["digest_runs"].c["team_id"],
    Base.metadata.tables["digest_runs"].c["window_end"],
    Base.metadata.tables["digest_runs"].c["id"],
)
Index(
    "ix_retention_work_team_due",
    Base.metadata.tables["work_items"].c["team_id"],
    Base.metadata.tables["work_items"].c["state"],
    Base.metadata.tables["work_items"].c["due_at"],
)
Index(
    "ix_retention_work_created",
    Base.metadata.tables["work_items"].c["team_id"],
    Base.metadata.tables["work_items"].c["created_at"],
    Base.metadata.tables["work_items"].c["id"],
)
Index(
    "ix_retention_work_finished",
    Base.metadata.tables["work_items"].c["team_id"],
    Base.metadata.tables["work_items"].c["completed_at"],
    Base.metadata.tables["work_items"].c["id"],
)
Index(
    "ix_retention_work_entity",
    Base.metadata.tables["work_items"].c["team_id"],
    Base.metadata.tables["work_items"].c["kind"],
    Base.metadata.tables["work_items"].c["entity_id"],
    Base.metadata.tables["work_items"].c["state"],
)
Index(
    "ix_retention_notification_event",
    Base.metadata.tables["notifications"].c["event_id"],
    Base.metadata.tables["notifications"].c["id"],
)
Index("ix_retention_notification_work", Base.metadata.tables["notifications"].c["work_id"])
Index(
    "ix_retention_notification_finished",
    Base.metadata.tables["notifications"].c["team_id"],
    Base.metadata.tables["notifications"].c["finished_at"],
    Base.metadata.tables["notifications"].c["id"],
)
Index(
    "ix_retention_attempt_finished",
    Base.metadata.tables["delivery_attempts"].c["finished_at"],
    Base.metadata.tables["delivery_attempts"].c["id"],
)
Index(
    "ix_retention_execution_event",
    Base.metadata.tables["rule_executions"].c["event_id"],
    Base.metadata.tables["rule_executions"].c["id"],
)
Index(
    "ix_retention_execution_occurrence", Base.metadata.tables["rule_executions"].c["occurrence_id"]
)
Index(
    "ix_retention_operation_notification",
    Base.metadata.tables["notification_operations"].c["notification_id"],
    Base.metadata.tables["notification_operations"].c["id"],
)
Index("ix_retention_digest_item_event", Base.metadata.tables["digest_items"].c["event_id"])
Index(
    "ix_retention_digest_item_occurrence", Base.metadata.tables["digest_items"].c["occurrence_id"]
)
Index(
    "ix_retention_digest_receipt_event",
    Base.metadata.tables["digest_receipts"].c["event_id"],
    Base.metadata.tables["digest_receipts"].c["occurrence_id"],
)
Index(
    "ix_retention_action_event",
    Base.metadata.tables["event_actions"].c["event_id"],
    Base.metadata.tables["event_actions"].c["id"],
)
Index(
    "ix_retention_occurrence_event",
    Base.metadata.tables["event_occurrences"].c["event_id"],
    Base.metadata.tables["event_occurrences"].c["id"],
)
Index(
    "ix_retention_blob_deleted",
    Base.metadata.tables["blobs"].c["team_id"],
    Base.metadata.tables["blobs"].c["deleted_at"],
    Base.metadata.tables["blobs"].c["id"],
)
