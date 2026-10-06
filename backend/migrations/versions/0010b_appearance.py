"""Persist installation appearance with optimistic concurrency."""

import sqlalchemy as sa
from alembic import op

revision = "0010b"
down_revision = "0010a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch:
        batch.add_column(
            sa.Column("appearance_palette", sa.String(16), nullable=False, server_default="blue")
        )
        batch.add_column(
            sa.Column("appearance_mode", sa.String(8), nullable=False, server_default="light")
        )
        batch.add_column(
            sa.Column("appearance_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_check_constraint(
            "ck_team_palette",
            "appearance_palette IN ('blue','teal','green','violet','wine','slate')",
        )
        batch.create_check_constraint("ck_team_mode", "appearance_mode IN ('light','dark')")
        batch.create_check_constraint("ck_team_appearance_version", "appearance_version > 0")


def downgrade():
    with op.batch_alter_table("teams") as batch:
        for name in ("ck_team_palette", "ck_team_mode", "ck_team_appearance_version"):
            batch.drop_constraint(name, type_="check")
        for name in ("appearance_palette", "appearance_mode", "appearance_version"):
            batch.drop_column(name)
