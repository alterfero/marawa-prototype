from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

from app.db import build_engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRE_INTERVAL_REVISION = "20260802_0013"


def _upgrade(engine: sa.Engine, revision: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_recording_year_interval_migration_converts_existing_story_dates(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'legacy-dates.db'}")
    _upgrade(engine, PRE_INTERVAL_REVISION)

    metadata = sa.MetaData()
    metadata.reflect(bind=engine, only=["datasets", "stories"])
    datasets = metadata.tables["datasets"]
    stories = metadata.tables["stories"]
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        connection.execute(
            datasets.insert().values(
                id="dataset-1",
                version=1,
                status="active",
                notes_json={},
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            stories.insert(),
            [
                {
                    "id": "story-1",
                    "dataset_id": "dataset-1",
                    "source_row_number": 1,
                    "fields_json": {"date of recording": "4 March 1998"},
                    "row_hash": "",
                    "completeness": "incomplete",
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "story-2",
                    "dataset_id": "dataset-1",
                    "source_row_number": 2,
                    "fields_json": {"date of recording": "1971-05-03"},
                    "row_hash": "",
                    "completeness": "incomplete",
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )

    _upgrade(engine, "head")

    migrated_metadata = sa.MetaData()
    migrated_metadata.reflect(bind=engine, only=["stories"])
    migrated_stories = migrated_metadata.tables["stories"]
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(
                migrated_stories.c.id,
                migrated_stories.c.fields_json,
                migrated_stories.c.recording_year_start,
                migrated_stories.c.recording_year_end,
            ).order_by(migrated_stories.c.id)
        ).mappings().all()

    assert [
        (row["recording_year_start"], row["recording_year_end"], row["fields_json"]["date of recording"])
        for row in rows
    ] == [
        (1998, 1998, "[1998, 1998]"),
        (1971, 1971, "[1971, 1971]"),
    ]
