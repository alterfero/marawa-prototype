"""add durable keyword management

Revision ID: 20260802_0012
Revises: 20260802_0011
Create Date: 2026-08-02 14:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_0012"
down_revision = "20260802_0011"
branch_labels = None
depends_on = None


keyword_confirmation_status_enum = sa.Enum(
    "unconfirmed",
    "canonical",
    name="keywordconfirmationstatus",
    native_enum=False,
)


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def upgrade() -> None:
    _set_sqlite_foreign_keys(False)
    try:
        with op.batch_alter_table("keywords", recreate="auto") as batch_op:
            batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
            batch_op.add_column(
                sa.Column(
                    "confirmation_status",
                    keyword_confirmation_status_enum,
                    nullable=False,
                    server_default="unconfirmed",
                )
            )
            batch_op.create_index("ix_keywords_confirmation_status", ["confirmation_status"], unique=False)
    finally:
        _set_sqlite_foreign_keys(True)


def downgrade() -> None:
    _set_sqlite_foreign_keys(False)
    try:
        with op.batch_alter_table("keywords", recreate="auto") as batch_op:
            batch_op.drop_index("ix_keywords_confirmation_status")
            batch_op.drop_column("confirmation_status")
            batch_op.drop_column("version")
    finally:
        _set_sqlite_foreign_keys(True)
