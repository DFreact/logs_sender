"""Output adapters, encrypted channels and versioned templates."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def identity():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), nullable=False),
    ]


def upgrade():
    op.create_table(
        "output_adapters",
        *identity(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("visible", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("team_id", "kind", name="uq_output_adapter"),
        sa.CheckConstraint("kind IN ('SMTP','TELEGRAM','WEBHOOK')", name="ck_output_kind"),
    )
    op.create_table(
        "templates",
        *identity(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
    )
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("templates.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_template_version"),
    )
    op.create_table(
        "notification_channels",
        *identity(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("secret_version", sa.Integer(), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("templates.id")),
        sa.Column("health_status", sa.String(16), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_channel_team", "notification_channels", ["team_id", "id"])


def downgrade():
    for table in ("notification_channels", "template_versions", "templates", "output_adapters"):
        op.drop_table(table)
