"""add user version for optimistic updates

Revision ID: 20260909_0017
Revises: 20260909_0016
Create Date: 2026-09-09 10:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_0017"
down_revision = "20260909_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("version")
