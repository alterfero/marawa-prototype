"""Persist hourly visitor activity.

Revision ID: 20261013_0018
Revises: 20260909_0017
"""

from alembic import op
import sqlalchemy as sa


revision = "20261013_0018"
down_revision = "20260909_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_activity",
        sa.Column("hour_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("browser_key", sa.String(64), nullable=False),
        sa.Column("identity_key", sa.String(36), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("hour_start", "browser_key", "identity_key"),
    )


def downgrade() -> None:
    op.drop_table("usage_activity")
