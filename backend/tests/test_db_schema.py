from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db import Dataset, DatasetStatus, Trope, build_engine, build_session_factory, initialize_database


def test_initialize_database_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "schema.db"
    engine = build_engine(f"sqlite:///{db_path}")

    initialize_database(engine)

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "datasets",
        "jobs",
        "keywords",
        "stories",
        "story_keywords",
        "story_tropes",
        "term_embeddings",
        "term_similarity_cache",
        "tropes",
    }

    with engine.connect() as connection:
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        alembic_version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    assert busy_timeout == 5000
    assert str(journal_mode).lower() == "wal"
    assert alembic_version == "20260608_0001"


def test_only_one_active_dataset_is_allowed(tmp_path) -> None:
    db_path = tmp_path / "datasets.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    SessionLocal = build_session_factory(engine)

    with SessionLocal() as session:
        session.add(Dataset(status=DatasetStatus.ACTIVE))
        session.commit()

        session.add(Dataset(status=DatasetStatus.ACTIVE))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("Expected a unique constraint violation for a second active dataset.")


def test_trope_normalized_text_is_unique(tmp_path) -> None:
    db_path = tmp_path / "tropes.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    SessionLocal = build_session_factory(engine)

    with SessionLocal() as session:
        session.add(Trope(text="Sky Woman"))
        session.commit()

        session.add(Trope(text="  sky\twoman  "))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("Expected a unique constraint violation for duplicate normalized trope text.")
