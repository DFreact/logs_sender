"""Allow the MAX bot output adapter without modifying existing channels."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("output_adapters") as batch:
        batch.drop_constraint("ck_output_kind", type_="check")
        batch.create_check_constraint(
            "ck_output_kind", "kind IN ('SMTP','TELEGRAM','MAX','WEBHOOK')"
        )


def downgrade():
    bind = op.get_bind()
    for table in ("output_adapters", "notification_channels", "templates", "notifications"):
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table} WHERE kind = 'MAX'")):
            raise RuntimeError("MAX_DOWNGRADE_REQUIRES_REMOVAL")
    with op.batch_alter_table("output_adapters") as batch:
        batch.drop_constraint("ck_output_kind", type_="check")
        batch.create_check_constraint("ck_output_kind", "kind IN ('SMTP','TELEGRAM','WEBHOOK')")
