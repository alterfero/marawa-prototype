"""add theme embeddings

Revision ID: 20260802_0013
Revises: 20260802_0012
Create Date: 2026-08-02 16:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_0013"
down_revision = "20260802_0012"
branch_labels = None
depends_on = None


_EXACTLY_ONE_TERM = (
    "(trope_id IS NOT NULL AND theme_id IS NULL AND keyword_id IS NULL) "
    "OR (trope_id IS NULL AND theme_id IS NOT NULL AND keyword_id IS NULL) "
    "OR (trope_id IS NULL AND theme_id IS NULL AND keyword_id IS NOT NULL)"
)


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _set_sqlite_foreign_keys(False)
        try:
            with op.batch_alter_table("term_embeddings", recreate="always") as batch_op:
                batch_op.drop_constraint("ck_term_embeddings_exactly_one_term", type_="check")
                batch_op.add_column(sa.Column("theme_id", sa.String(length=36), nullable=True))
                batch_op.create_foreign_key(
                    "fk_term_embeddings_theme_id_themes",
                    "themes",
                    ["theme_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
                batch_op.create_index("uq_term_embeddings_theme_model", ["theme_id", "model_name"], unique=True)
                batch_op.create_check_constraint("ck_term_embeddings_exactly_one_term", _EXACTLY_ONE_TERM)
        finally:
            _set_sqlite_foreign_keys(True)
        return

    op.add_column("term_embeddings", sa.Column("theme_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_term_embeddings_theme_id_themes",
        "term_embeddings",
        "themes",
        ["theme_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("ck_term_embeddings_exactly_one_term", "term_embeddings", type_="check")
    op.create_check_constraint("ck_term_embeddings_exactly_one_term", "term_embeddings", _EXACTLY_ONE_TERM)
    op.create_index("uq_term_embeddings_theme_model", "term_embeddings", ["theme_id", "model_name"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _set_sqlite_foreign_keys(False)
        try:
            with op.batch_alter_table("term_embeddings", recreate="always") as batch_op:
                batch_op.drop_index("uq_term_embeddings_theme_model")
                batch_op.drop_constraint("fk_term_embeddings_theme_id_themes", type_="foreignkey")
                batch_op.drop_constraint("ck_term_embeddings_exactly_one_term", type_="check")
                batch_op.drop_column("theme_id")
                batch_op.create_check_constraint(
                    "ck_term_embeddings_exactly_one_term",
                    "(trope_id IS NOT NULL AND keyword_id IS NULL) OR (trope_id IS NULL AND keyword_id IS NOT NULL)",
                )
        finally:
            _set_sqlite_foreign_keys(True)
        return

    op.drop_index("uq_term_embeddings_theme_model", table_name="term_embeddings")
    op.drop_constraint("fk_term_embeddings_theme_id_themes", "term_embeddings", type_="foreignkey")
    op.drop_constraint("ck_term_embeddings_exactly_one_term", "term_embeddings", type_="check")
    op.drop_column("term_embeddings", "theme_id")
    op.create_check_constraint(
        "ck_term_embeddings_exactly_one_term",
        "term_embeddings",
        "(trope_id IS NOT NULL AND keyword_id IS NULL) OR (trope_id IS NULL AND keyword_id IS NOT NULL)",
    )
