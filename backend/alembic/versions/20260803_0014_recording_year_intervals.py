"""store story recording dates as year intervals

Revision ID: 20260803_0014
Revises: 20260802_0013
Create Date: 2026-08-03 12:00:00
"""

from __future__ import annotations

import json
import re

from alembic import op
import sqlalchemy as sa


revision = "20260803_0014"
down_revision = "20260802_0013"
branch_labels = None
depends_on = None


DATE_OF_RECORDING_FIELD = "date of recording"
MIN_RECORDING_YEAR = 1800
MAX_RECORDING_YEAR = 2050
YEAR_INTERVAL_RE = re.compile(r"^\[\s*(\d{4})\s*,\s*(\d{4})\s*\]$")
LEGACY_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_INTERVAL_CHECK = (
    "(recording_year_start IS NULL AND recording_year_end IS NULL) "
    "OR (recording_year_start IS NOT NULL AND recording_year_end IS NOT NULL "
    "AND recording_year_end >= recording_year_start)"
)


def _read_interval(value: object) -> tuple[int, int] | None:
    text = str(value or "").strip()
    if not text:
        return None

    match = YEAR_INTERVAL_RE.fullmatch(text)
    if match is not None:
        year1, year2 = (int(part) for part in match.groups())
        if MIN_RECORDING_YEAR <= year1 <= year2 <= MAX_RECORDING_YEAR:
            return year1, year2

    legacy_year = LEGACY_YEAR_RE.search(text)
    if legacy_year is None:
        return None
    year = int(legacy_year.group(1))
    if not MIN_RECORDING_YEAR <= year <= MAX_RECORDING_YEAR:
        return None
    return year, year


def _as_fields(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _migrate_existing_story_dates() -> None:
    bind = op.get_bind()
    stories = sa.table(
        "stories",
        sa.column("id", sa.String()),
        sa.column("fields_json", sa.JSON()),
        sa.column("recording_year_start", sa.Integer()),
        sa.column("recording_year_end", sa.Integer()),
    )
    existing_stories = bind.execute(sa.select(stories.c.id, stories.c.fields_json)).mappings()
    for story in existing_stories:
        fields = _as_fields(story["fields_json"])
        interval = _read_interval(fields.get(DATE_OF_RECORDING_FIELD, ""))
        if interval is None:
            fields[DATE_OF_RECORDING_FIELD] = ""
            values = {
                "fields_json": fields,
                "recording_year_start": None,
                "recording_year_end": None,
            }
        else:
            year1, year2 = interval
            fields[DATE_OF_RECORDING_FIELD] = f"[{year1}, {year2}]"
            values = {
                "fields_json": fields,
                "recording_year_start": year1,
                "recording_year_end": year2,
            }
        bind.execute(stories.update().where(stories.c.id == story["id"]).values(**values))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot add a check constraint without rebuilding ``stories``.
        # Rebuilding the parent table can cascade-delete story assignments when
        # the database enables foreign keys, so use additive columns and paired
        # triggers instead.
        op.add_column("stories", sa.Column("recording_year_start", sa.Integer(), nullable=True))
        op.add_column("stories", sa.Column("recording_year_end", sa.Integer(), nullable=True))
        op.create_index("ix_stories_recording_year_start", "stories", ["recording_year_start"])
        op.create_index("ix_stories_recording_year_end", "stories", ["recording_year_end"])
        op.execute(
            """
            CREATE TRIGGER trg_stories_recording_year_interval_insert
            BEFORE INSERT ON stories
            WHEN NOT (
                (NEW.recording_year_start IS NULL AND NEW.recording_year_end IS NULL)
                OR (NEW.recording_year_start IS NOT NULL AND NEW.recording_year_end IS NOT NULL
                    AND NEW.recording_year_end >= NEW.recording_year_start)
            )
            BEGIN
                SELECT RAISE(ABORT, 'Invalid recording year interval');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_stories_recording_year_interval_update
            BEFORE UPDATE OF recording_year_start, recording_year_end ON stories
            WHEN NOT (
                (NEW.recording_year_start IS NULL AND NEW.recording_year_end IS NULL)
                OR (NEW.recording_year_start IS NOT NULL AND NEW.recording_year_end IS NOT NULL
                    AND NEW.recording_year_end >= NEW.recording_year_start)
            )
            BEGIN
                SELECT RAISE(ABORT, 'Invalid recording year interval');
            END
            """
        )
    else:
        op.add_column("stories", sa.Column("recording_year_start", sa.Integer(), nullable=True))
        op.add_column("stories", sa.Column("recording_year_end", sa.Integer(), nullable=True))
        op.create_index("ix_stories_recording_year_start", "stories", ["recording_year_start"])
        op.create_index("ix_stories_recording_year_end", "stories", ["recording_year_end"])
        op.create_check_constraint("ck_stories_recording_year_interval", "stories", _INTERVAL_CHECK)

    _migrate_existing_story_dates()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER trg_stories_recording_year_interval_update")
        op.execute("DROP TRIGGER trg_stories_recording_year_interval_insert")
        op.drop_index("ix_stories_recording_year_end", table_name="stories")
        op.drop_index("ix_stories_recording_year_start", table_name="stories")
        op.drop_column("stories", "recording_year_end")
        op.drop_column("stories", "recording_year_start")
        return

    op.drop_constraint("ck_stories_recording_year_interval", "stories", type_="check")
    op.drop_index("ix_stories_recording_year_end", table_name="stories")
    op.drop_index("ix_stories_recording_year_start", table_name="stories")
    op.drop_column("stories", "recording_year_end")
    op.drop_column("stories", "recording_year_start")
