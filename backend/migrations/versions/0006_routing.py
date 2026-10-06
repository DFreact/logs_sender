"""Routing rules, immutable versions and action decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "routing_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_routing_priority", "routing_rules", ["team_id", "priority", "id"])
    with op.batch_alter_table("rule_versions") as batch:
        batch.alter_column("rule_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("routing_rule_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_version_routing", "routing_rules", ["routing_rule_id"], ["id"])
        batch.create_unique_constraint("uq_routing_version", ["routing_rule_id", "version"])
        batch.create_check_constraint(
            "ck_version_owner",
            "(rule_id IS NOT NULL AND routing_rule_id IS NULL) OR "
            "(rule_id IS NULL AND routing_rule_id IS NOT NULL)",
        )
    op.create_table(
        "rule_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column(
            "occurrence_id", sa.Uuid(), sa.ForeignKey("event_occurrences.id"), nullable=False
        ),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("rule_versions.id"), nullable=False),
        sa.Column("action_index", sa.Integer(), nullable=False),
        sa.Column("scope_key", sa.Uuid(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "event_id", "version_id", "action_index", "scope_key", name="uq_rule_effect"
        ),
    )
    op.create_index("ix_execution_occurrence", "rule_executions", ["occurrence_id", "id"])


def downgrade():
    op.drop_table("rule_executions")
    op.execute("DELETE FROM rule_versions WHERE routing_rule_id IS NOT NULL")
    with op.batch_alter_table("rule_versions") as batch:
        batch.drop_constraint("ck_version_owner", type_="check")
        batch.drop_constraint("uq_routing_version", type_="unique")
        batch.drop_constraint("fk_version_routing", type_="foreignkey")
        batch.drop_column("routing_rule_id")
        batch.alter_column("rule_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("routing_rules")
