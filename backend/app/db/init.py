from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from app.db.session import engine


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _build_alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url)
    return config


def initialize_database(db_engine: Engine | None = None) -> None:
    target_engine = db_engine or engine
    config = _build_alembic_config(str(target_engine.url))
    with target_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
