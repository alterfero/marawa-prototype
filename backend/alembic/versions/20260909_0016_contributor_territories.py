"""add contributor territory assignments

Revision ID: 20260909_0016
Revises: 20260810_0015
Create Date: 2026-09-09 09:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_0016"
down_revision = "20260810_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "territories",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("territories")
