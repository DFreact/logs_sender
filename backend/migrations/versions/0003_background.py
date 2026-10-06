"""Durable work, immutable blobs and background component state."""

import sqlalchemy as sa
from alembic import op

revision = "0003_background"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "component_heartbeats",
        sa.Column("component", sa.String(length=24), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("component", "instance_id"),
    )
    op.create_index(
        op.f("ix_component_heartbeats_checked_at"),
        "component_heartbeats",
        ["checked_at"],
        unique=False,
    )
    op.create_table(
        "storage_scans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shard", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "blobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("owner_kind", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('STAGING','READY','DELETING','DELETED')", name="ck_blob_state"
        ),
        sa.CheckConstraint("size IS NULL OR size >= 0", name="ck_blob_size"),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_blob_state_created", "blobs", ["state", "created_at"], unique=False)
    op.create_table(
        "work_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=180), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("publication_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_token", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="ck_work_state"
        ),
        sa.CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_work_attempts"),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "dedupe_key", name="uq_work_team_dedupe"),
    )
    op.create_index("ix_work_due", "work_items", ["state", "due_at"], unique=False)
    op.create_index("ix_work_lease", "work_items", ["state", "lease_until"], unique=False)
    op.bulk_insert(
        sa.table(
            "storage_scans",
            sa.column("id", sa.Integer()),
            sa.column("shard", sa.Integer()),
            sa.column("cursor", sa.String()),
        ),
        [{"id": 1, "shard": 0, "cursor": ""}],
    )


def downgrade():
    op.drop_index("ix_work_lease", table_name="work_items")
    op.drop_index("ix_work_due", table_name="work_items")
    op.drop_table("work_items")
    op.drop_index("ix_blob_state_created", table_name="blobs")
    op.drop_table("blobs")
    op.drop_table("storage_scans")
    op.drop_index(op.f("ix_component_heartbeats_checked_at"), table_name="component_heartbeats")
    op.drop_table("component_heartbeats")
