"""add story completeness

Revision ID: 20260715_0005
Revises: 20260617_0004
Create Date: 2026-07-15 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0005"
down_revision = "20260617_0004"
branch_labels = None
depends_on = None


story_completeness_enum = sa.Enum(
    "incomplete",
    "pending review",
    "complete",
    name="storycompleteness",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("stories", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "completeness",
                story_completeness_enum,
                nullable=False,
                server_default="incomplete",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("stories", recreate="auto") as batch_op:
        batch_op.drop_column("completeness")
