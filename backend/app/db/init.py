from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError
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
    try:
        with target_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    except OperationalError as exc:
        if _looks_like_missing_postgres_database(exc):
            raise RuntimeError(
                "PostgreSQL is reachable, but the configured database does not exist. "
                "Create it first with `scripts/local_postgres.sh create-dev-db`, or point "
                "`MARAWA_DATABASE_URL` at an existing database such as the URL printed by "
                "`scripts/local_postgres.sh url dev`."
            ) from exc
        raise


def _looks_like_missing_postgres_database(exc: OperationalError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    return 'database "' in message and "does not exist" in message
