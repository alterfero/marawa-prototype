"""backfill themes added after the initial theme migration

Revision ID: 20260729_0009
Revises: 20260729_0008
Create Date: 2026-07-29 13:00:00
"""

from datetime import datetime, timezone
import re
import uuid

from alembic import op
import sqlalchemy as sa


revision = "20260729_0009"
down_revision = "20260729_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Recover raw ``Thème`` values loaded after revision 0008 was applied.

    The first theme migration backfilled the stories that existed at upgrade
    time. This idempotent pass also preserves stories subsequently loaded by
    an older application process, without changing any existing assignments.
    """
    bind = op.get_bind()
    metadata = sa.MetaData()
    stories = sa.Table("stories", metadata, autoload_with=bind)
    themes = sa.Table("themes", metadata, autoload_with=bind)
    story_themes = sa.Table("story_themes", metadata, autoload_with=bind)

    themes_by_dataset_and_text = {
        (row["dataset_id"], row["normalized_text"]): row["id"]
        for row in bind.execute(
            sa.select(themes.c.id, themes.c.dataset_id, themes.c.normalized_text)
        ).mappings()
    }
    existing_links = {
        (row["story_id"], row["theme_id"])
        for row in bind.execute(sa.select(story_themes.c.story_id, story_themes.c.theme_id)).mappings()
    }
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

            link_key = (row["id"], theme_id)
            if link_key in existing_links:
                continue
            bind.execute(
                story_themes.insert().values(
                    story_id=row["id"],
                    theme_id=theme_id,
                    position=position,
                    created_at=now,
                    updated_at=now,
                )
            )
            existing_links.add(link_key)

    bind.execute(themes.update().values(cached_story_count=0))
    for row in bind.execute(
        sa.select(story_themes.c.theme_id, sa.func.count())
        .group_by(story_themes.c.theme_id)
    ).mappings():
        bind.execute(
            themes.update()
            .where(themes.c.id == row["theme_id"])
            .values(cached_story_count=row["count"])
        )


def downgrade() -> None:
    # The imported theme data must remain intact on downgrade.
    pass


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
