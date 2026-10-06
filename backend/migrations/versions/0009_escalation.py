"""Event actions and durable escalation schedules."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "escalation_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "escalation_policy_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["escalation_policies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "version", name="uq_escalation_version"),
    )
    op.create_table(
        "event_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=False),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "operation_key", name="uq_event_action"),
    )
    with op.batch_alter_table("event_actions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_event_actions_event_id"), ["event_id"], unique=False)

    op.create_table(
        "escalation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "state IN ('ACTIVE','COMPLETED','STOPPED')", name="ck_escalation_run_state"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["rule_executions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["escalation_policies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id"),
    )
    with op.batch_alter_table("escalation_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_escalation_runs_event_id"), ["event_id"], unique=False)

    op.create_table(
        "escalation_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("channel_name", sa.String(length=120), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "state IN ('WAITING','QUEUED','CANCELLED')", name="ck_escalation_step_state"
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["escalation_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_escalation_step"),
    )
    with op.batch_alter_table("escalation_steps", schema=None) as batch_op:
        batch_op.create_index("ix_escalation_step_due", ["state", "due_at"], unique=False)

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        batch_op.add_column(sa.Column("acknowledged_by", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("state_changed_by", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key("fk_event_ack_user", "users", ["acknowledged_by"], ["id"])
        batch_op.create_foreign_key("fk_event_state_user", "users", ["state_changed_by"], ["id"])

    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("escalation_run_id", sa.Uuid(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_notifications_escalation_run_id"), ["escalation_run_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_notification_escalation", "escalation_runs", ["escalation_run_id"], ["id"]
        )


def downgrade():
    # Downgrade must not leave escalation notifications runnable without their guard.
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM notifications WHERE escalation_run_id IS NOT NULL "
            "AND status = 'PROCESSING'"
        )
    ):
        raise RuntimeError("ACTIVE_ESCALATION_DELIVERY")
    op.execute(
        "UPDATE work_items SET state = 'SUCCEEDED', completed_at = CURRENT_TIMESTAMP "
        "WHERE id IN (SELECT work_id FROM notifications WHERE escalation_run_id IS NOT NULL "
        "AND status IN ('PENDING','RETRYING'))"
    )
    op.execute(
        "UPDATE notifications SET status = 'CANCELLED', finished_at = CURRENT_TIMESTAMP, "
        "failure_code = NULL, version = version + 1 WHERE escalation_run_id IS NOT NULL "
        "AND status IN ('PENDING','RETRYING')"
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_constraint("fk_notification_escalation", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_notifications_escalation_run_id"))
        batch_op.drop_column("escalation_run_id")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_constraint("fk_event_state_user", type_="foreignkey")
        batch_op.drop_constraint("fk_event_ack_user", type_="foreignkey")
        batch_op.drop_column("state_changed_at")
        batch_op.drop_column("state_changed_by")
        batch_op.drop_column("acknowledged_at")
        batch_op.drop_column("acknowledged_by")
        batch_op.drop_column("version")

    with op.batch_alter_table("escalation_steps", schema=None) as batch_op:
        batch_op.drop_index("ix_escalation_step_due")

    op.drop_table("escalation_steps")
    with op.batch_alter_table("escalation_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_escalation_runs_event_id"))

    op.drop_table("escalation_runs")
    with op.batch_alter_table("event_actions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_event_actions_event_id"))

    op.drop_table("event_actions")
    op.drop_table("escalation_policy_versions")
    op.drop_table("escalation_policies")
