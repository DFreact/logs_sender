"""Users, teams, revocable sessions, persistent login throttles and audit."""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0002_identity"
down_revision = "0001_bootstrap"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
    )
    op.bulk_insert(
        sa.table("teams", sa.column("id", sa.Uuid()), sa.column("name", sa.String())),
        [{"id": UUID("00000000-0000-0000-0000-000000000001"), "name": "default"}],
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "role IN ('ADMINISTRATOR','OPERATOR','VIEWER')", name="ck_membership_role"
        ),
    )
    op.create_index("ix_memberships_team_role", "memberships", ["team_id", "role"])
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id", "team_id"], ["memberships.user_id", "memberships.team_id"]
        ),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_sessions_user", "sessions", ["user_id"])
    op.create_table(
        "login_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_index("ix_login_limits_window_start", "login_limits", ["window_start"])
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("request_ip", sa.String(45), nullable=True),
        sa.Column("request_id", sa.String(32), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_entries_expires_at", "audit_entries", ["expires_at"])
    op.create_index("ix_audit_team_timestamp", "audit_entries", ["team_id", "timestamp", "id"])


def downgrade():
    for table in ["audit_entries", "login_limits", "sessions", "memberships", "users", "teams"]:
        op.drop_table(table)
