from pathlib import Path
import sqlite3

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


settings = get_settings()


def _configure_sqlite_connection(dbapi_connection: sqlite3.Connection, _: object) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        try:
            cursor.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
    finally:
        cursor.close()


def _ensure_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return
    database = url.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def build_engine(database_url: str) -> Engine:
    _ensure_sqlite_directory(database_url)
    backend_name = make_url(database_url).get_backend_name()
    engine_kwargs = {"future": True, "pool_pre_ping": backend_name != "sqlite"}
    db_engine = create_engine(database_url, **engine_kwargs)
    if backend_name == "sqlite":
        event.listen(db_engine, "connect", _configure_sqlite_connection)
    return db_engine


def build_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False)


engine = build_engine(settings.database_url)
SessionLocal = build_session_factory(engine)
