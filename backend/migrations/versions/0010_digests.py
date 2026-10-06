"""digests"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "digest_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_digest_definitions_next_end"), "digest_definitions", ["next_end"], unique=False
    )
    op.create_table(
        "digest_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["digest_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("definition_id", "version", name="uq_digest_version"),
    )
    op.create_table(
        "digest_receipts",
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["event_occurrences.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("occurrence_id"),
    )
    op.create_index(
        "ix_digest_receipt_window", "digest_receipts", ["team_id", "received_at"], unique=False
    )
    op.create_table(
        "digest_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operation_actor", sa.Uuid(), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column("partial", sa.Boolean(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.Uuid(), nullable=True),
        sa.Column("operation_version", sa.Integer(), nullable=True),
        sa.Column("operation_action", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "state IN ('WAITING','ATTENTION','BUILDING','READY','EMPTY','SKIPPED')",
            name="ck_digest_state",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["digest_definitions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
        ),
        sa.ForeignKeyConstraint(
            ["operation_actor"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("definition_id", "window_start", "window_end", name="uq_digest_window"),
        sa.UniqueConstraint("notification_id"),
    )
    op.create_index("ix_digest_run_state", "digest_runs", ["state", "window_end"], unique=False)
    op.create_table(
        "digest_items",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["event_occurrences.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["digest_runs.id"],
        ),
        sa.PrimaryKeyConstraint("run_id", "occurrence_id"),
    )


def downgrade():
    op.drop_table("digest_items")
    op.drop_index("ix_digest_run_state", table_name="digest_runs")
    op.drop_table("digest_runs")
    op.drop_index("ix_digest_receipt_window", table_name="digest_receipts")
    op.drop_table("digest_receipts")
    op.drop_table("digest_versions")
    op.drop_index(op.f("ix_digest_definitions_next_end"), table_name="digest_definitions")
    op.drop_table("digest_definitions")
