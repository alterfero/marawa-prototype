"""Durable CSV injection reviews and idempotent application results."""
from alembic import op
import sqlalchemy as sa

revision = "20261023_0019"
down_revision = "20261013_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "csv_injections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id", ondelete="SET NULL")),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(512)),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("csv_injections")
