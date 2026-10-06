"""Notifications, durable delivery attempts and idempotent manual operations."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("events.id")),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("rule_executions.id"), unique=True),
        sa.Column(
            "channel_id", sa.Uuid(), sa.ForeignKey("notification_channels.id"), nullable=False
        ),
        sa.Column("channel_name", sa.String(120), nullable=False),
        sa.Column("channel_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), sa.ForeignKey("template_versions.id")),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("prepared", sa.JSON()),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True)),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("retry_delays", sa.JSON(), nullable=False),
        sa.Column("jitter_percent", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(40)),
        sa.Column("uncertain", sa.Boolean(), nullable=False),
        sa.Column("work_id", sa.Uuid(), sa.ForeignKey("work_items.id")),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','SENT','FAILED','RETRYING','CANCELLED')",
            name="ck_notification_status",
        ),
        sa.CheckConstraint(
            "generation > 0 AND attempt_count >= 0", name="ck_notification_attempts"
        ),
    )
    op.create_index(
        "ix_notification_team_created", "notifications", ["team_id", "created_at", "id"]
    )
    op.create_index("ix_notification_due", "notifications", ["status", "due_at"])
    op.create_index("ix_notification_lease", "notifications", ["status", "lease_until"])
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("notification_id", sa.Uuid(), sa.ForeignKey("notifications.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(40)),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("secret_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "notification_id", "generation", "attempt_number", name="uq_delivery_attempt"
        ),
        sa.CheckConstraint(
            "status IN ('PROCESSING','SENT','FAILED','UNKNOWN')", name="ck_delivery_status"
        ),
    )
    op.create_index(
        "ix_delivery_notification_started",
        "delivery_attempts",
        ["notification_id", "started_at", "id"],
    )
    op.create_table(
        "notification_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("notification_id", sa.Uuid(), sa.ForeignKey("notifications.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("team_id", "operation_key", name="uq_notification_operation"),
    )


def downgrade():
    op.drop_table("notification_operations")
    op.drop_table("delivery_attempts")
    op.drop_table("notifications")
    # Completed and pending wake-ups are meaningless without their notification.
    op.execute("DELETE FROM work_items WHERE kind = 'DELIVER'")
