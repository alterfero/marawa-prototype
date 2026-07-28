"""rename confirmed trope status to canonical

Revision ID: 20260727_0007
Revises: 20260721_0006
Create Date: 2026-07-27 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0007"
down_revision = "20260721_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE tropes SET confirmation_status = 'canonical' "
            "WHERE confirmation_status = 'confirmed'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE tropes SET confirmation_status = 'confirmed' "
            "WHERE confirmation_status = 'canonical'"
        )
    )
