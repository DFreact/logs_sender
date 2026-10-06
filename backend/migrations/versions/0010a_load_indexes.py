"""Indexes and resumable chronological composition for large event archives.

Run during a maintenance window with application processes stopped.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010a"
down_revision = "0010"
branch_labels = None
depends_on = None
FIELDS = ("source_id", "status", "severity", "input_adapter")


def restart_unfinished():
    # Switching cursor ordering must never skip or duplicate a partly built composition.
    op.execute(
        "DELETE FROM digest_items WHERE run_id IN "
        "(SELECT id FROM digest_runs WHERE state = 'BUILDING')"
    )
    op.execute("UPDATE digest_runs SET cursor = NULL WHERE state = 'BUILDING'")


def upgrade():
    for field in FIELDS:
        op.create_index(
            f"ix_event_{field}_received", "events", ["team_id", field, "received_at", "id"]
        )
    op.create_index("ix_raw_pending_window", "raw_messages", ["team_id", "state", "received_at"])
    op.drop_index("ix_digest_receipt_window", table_name="digest_receipts")
    op.create_index(
        "ix_digest_receipt_window", "digest_receipts", ["team_id", "received_at", "occurrence_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public"))
        for field in ("subject", "sender"):
            op.create_index(
                f"ix_event_{field}_search",
                "events",
                [field],
                postgresql_using="gin",
                postgresql_ops={field: "public.gin_trgm_ops"},
            )
    restart_unfinished()


def downgrade():
    restart_unfinished()
    if op.get_bind().dialect.name == "postgresql":
        for field in ("subject", "sender"):
            op.drop_index(f"ix_event_{field}_search", table_name="events")
    op.drop_index("ix_digest_receipt_window", table_name="digest_receipts")
    op.create_index("ix_digest_receipt_window", "digest_receipts", ["team_id", "received_at"])
    op.drop_index("ix_raw_pending_window", table_name="raw_messages")
    for field in FIELDS:
        op.drop_index(f"ix_event_{field}_received", table_name="events")
    # pg_trgm may be shared by other schemas; never drop it here.
