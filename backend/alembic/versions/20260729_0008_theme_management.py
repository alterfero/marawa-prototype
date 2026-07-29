"""add durable theme management

Revision ID: 20260729_0008
Revises: 20260727_0007
Create Date: 2026-07-29 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
import re
import uuid


revision = "20260729_0008"
down_revision = "20260727_0007"
branch_labels = None
depends_on = None


theme_confirmation_status_enum = sa.Enum(
    "unconfirmed",
    "canonical",
    name="themeconfirmationstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "themes",
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.String(length=512), nullable=False),
        sa.Column(
            "confirmation_status",
            theme_confirmation_status_enum,
            nullable=False,
            server_default="unconfirmed",
        ),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("cached_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_themes_dataset_id"), "themes", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_themes_confirmation_status"), "themes", ["confirmation_status"], unique=False)
    op.create_index(op.f("ix_themes_created_by_user_id"), "themes", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_themes_updated_by_user_id"), "themes", ["updated_by_user_id"], unique=False)
    op.create_index("uq_themes_dataset_normalized_text", "themes", ["dataset_id", "normalized_text"], unique=True)

    op.create_table(
        "story_themes",
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("theme_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["theme_id"], ["themes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("story_id", "theme_id"),
        sa.UniqueConstraint("story_id", "theme_id", name="uq_story_themes_story_id_theme_id"),
    )

    _backfill_themes_from_story_fields()


def downgrade() -> None:
    op.drop_table("story_themes")
    op.drop_index("uq_themes_dataset_normalized_text", table_name="themes")
    op.drop_index(op.f("ix_themes_updated_by_user_id"), table_name="themes")
    op.drop_index(op.f("ix_themes_created_by_user_id"), table_name="themes")
    op.drop_index(op.f("ix_themes_confirmation_status"), table_name="themes")
    op.drop_index(op.f("ix_themes_dataset_id"), table_name="themes")
    op.drop_table("themes")


def _backfill_themes_from_story_fields() -> None:
    """Preserve existing imported theme cells when upgrading a live database."""
    bind = op.get_bind()
    metadata = sa.MetaData()
    stories = sa.Table("stories", metadata, autoload_with=bind)
    themes = sa.Table("themes", metadata, autoload_with=bind)
    story_themes = sa.Table("story_themes", metadata, autoload_with=bind)

    themes_by_dataset_and_text: dict[tuple[str, str], str] = {}
    story_counts: dict[str, int] = {}
    now = datetime.now(timezone.utc)

    rows = bind.execute(sa.select(stories.c.id, stories.c.dataset_id, stories.c.fields_json)).mappings()
    for row in rows:
        fields = row["fields_json"] or {}
        if not isinstance(fields, dict):
            continue
        for position, theme_text in enumerate(_split_themes(fields.get("Thème", ""))):
            marker = _normalize(theme_text)
            key = (row["dataset_id"], marker)
            theme_id = themes_by_dataset_and_text.get(key)
            if theme_id is None:
                theme_id = str(uuid.uuid4())
                themes_by_dataset_and_text[key] = theme_id
                bind.execute(
                    themes.insert().values(
                        id=theme_id,
                        dataset_id=row["dataset_id"],
                        version=1,
                        text=theme_text,
                        normalized_text=marker,
                        confirmation_status="unconfirmed",
                        cached_story_count=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
            bind.execute(
                story_themes.insert().values(
                    story_id=row["id"],
                    theme_id=theme_id,
                    position=position,
                    created_at=now,
                    updated_at=now,
                )
            )
            story_counts[theme_id] = story_counts.get(theme_id, 0) + 1

    for theme_id, count in story_counts.items():
        bind.execute(themes.update().where(themes.c.id == theme_id).values(cached_story_count=count))


def _split_themes(value: object) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    pieces = text.split("§§") if "§§" in text else re.split(r"[;\n]+", text)
    values: list[str] = []
    markers: set[str] = set()
    for piece in pieces:
        item = piece.strip(" \n;")
        marker = _normalize(item)
        if not marker or marker in markers:
            continue
        markers.add(marker)
        values.append(item)
    return values


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\ufeff", "").strip()).lower()
