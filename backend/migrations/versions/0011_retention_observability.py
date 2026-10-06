"""Bounded retention and transactional operational counters."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010b"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Seed counters from a consistent archive; no writes between seed and triggers.
        op.execute(
            "LOCK TABLE teams, work_items, events, notifications, raw_messages, "
            "event_occurrences IN SHARE ROW EXCLUSIVE MODE"
        )
    with op.batch_alter_table("raw_messages") as batch:
        batch.alter_column("blob_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("source_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_raw_retention_source", "sources", ["source_id"], ["id"])
        batch.create_index(
            "ix_raw_expiry",
            ["team_id", "expires_at", "id"],
            postgresql_where=sa.text("purged_at IS NULL"),
            sqlite_where=sa.text("purged_at IS NULL"),
        )
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("retiring", sa.Boolean(), nullable=False, server_default="false")
        )
        batch.create_index("ix_event_expiry", ["team_id", "expires_at", "id"])
    op.create_table(
        "retention_policies",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("scope", sa.String(36), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("inherit", sa.Boolean(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "retention_progress",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column("cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed", sa.BigInteger(), nullable=False),
        sa.Column("held", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "metric_counters",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("bucket", sa.Integer(), primary_key=True),
        sa.Column("value", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "health_samples",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("slot", sa.BigInteger(), primary_key=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
    )
    op.create_index("ix_health_sample_time", "health_samples", ["team_id", "measured_at"])
    # All foreign-key lookups used by bounded cleanup need an index too.
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
    from app.persistence.counter_schema_v1 import install

    install(connection, seed=True)


INDEXES = [
    ("ix_retention_rule_version_created", "rule_versions", ["created_at", "id"]),
    ("ix_retention_template_version_created", "template_versions", ["created_at", "id"]),
    ("ix_retention_escalation_version_created", "escalation_policy_versions", ["created_at", "id"]),
    ("ix_retention_digest_version_created", "digest_versions", ["created_at", "id"]),
    ("ix_retention_occurrence_rule_version", "event_occurrences", ["rule_version_id"]),
    ("ix_retention_execution_version", "rule_executions", ["version_id"]),
    ("ix_retention_notification_template", "notifications", ["template_version_id"]),
    ("ix_retention_digest_finished", "digest_runs", ["team_id", "window_end", "id"]),
    ("ix_retention_work_team_due", "work_items", ["team_id", "state", "due_at"]),
    ("ix_retention_work_created", "work_items", ["team_id", "created_at", "id"]),
    ("ix_retention_work_finished", "work_items", ["team_id", "completed_at", "id"]),
    ("ix_retention_work_entity", "work_items", ["team_id", "kind", "entity_id", "state"]),
    ("ix_retention_notification_event", "notifications", ["event_id", "id"]),
    ("ix_retention_notification_work", "notifications", ["work_id"]),
    ("ix_retention_notification_finished", "notifications", ["team_id", "finished_at", "id"]),
    ("ix_retention_attempt_finished", "delivery_attempts", ["finished_at", "id"]),
    ("ix_retention_execution_event", "rule_executions", ["event_id", "id"]),
    ("ix_retention_execution_occurrence", "rule_executions", ["occurrence_id"]),
    ("ix_retention_operation_notification", "notification_operations", ["notification_id", "id"]),
    ("ix_retention_digest_item_event", "digest_items", ["event_id"]),
    ("ix_retention_digest_item_occurrence", "digest_items", ["occurrence_id"]),
    ("ix_retention_digest_receipt_event", "digest_receipts", ["event_id", "occurrence_id"]),
    ("ix_retention_action_event", "event_actions", ["event_id", "id"]),
    ("ix_retention_occurrence_event", "event_occurrences", ["event_id", "id"]),
    ("ix_retention_blob_deleted", "blobs", ["team_id", "deleted_at", "id"]),
]


def downgrade():
    from app.persistence.counter_schema_v1 import uninstall

    connection = op.get_bind()
    # A downgrade cannot reconstruct original messages already removed by policy.
    if connection.scalar(sa.text("SELECT 1 FROM raw_messages WHERE blob_id IS NULL LIMIT 1")):
        raise RuntimeError("RETENTION_DOWNGRADE_REQUIRES_RESTORE")
    uninstall(connection)
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    for table in ["health_samples", "metric_counters", "retention_progress", "retention_policies"]:
        op.drop_table(table)
    with op.batch_alter_table("events") as batch:
        batch.drop_index("ix_event_expiry")
        batch.drop_column("retiring")
        batch.drop_column("expires_at")
    with op.batch_alter_table("raw_messages") as batch:
        batch.drop_index("ix_raw_expiry")
        batch.drop_constraint("fk_raw_retention_source", type_="foreignkey")
        for column in ["source_id", "purged_at", "expires_at"]:
            batch.drop_column(column)
        batch.alter_column("blob_id", existing_type=sa.Uuid(), nullable=False)
