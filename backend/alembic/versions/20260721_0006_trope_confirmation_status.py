"""add trope confirmation status and version

Revision ID: 20260721_0006
Revises: 20260715_0005
Create Date: 2026-07-21 10:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260721_0006"
down_revision = "20260715_0005"
branch_labels = None
depends_on = None


trope_confirmation_status_enum = sa.Enum(
    "unconfirmed",
    "confirmed",
    name="tropeconfirmationstatus",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("tropes", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch_op.add_column(
            sa.Column(
                "confirmation_status",
                trope_confirmation_status_enum,
                nullable=False,
                server_default="unconfirmed",
            )
        )
        batch_op.create_index("ix_tropes_confirmation_status", ["confirmation_status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("tropes", recreate="auto") as batch_op:
        batch_op.drop_index("ix_tropes_confirmation_status")
        batch_op.drop_column("confirmation_status")
        batch_op.drop_column("version")
