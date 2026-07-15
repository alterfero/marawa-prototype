from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import Dataset, DatasetStatus, Story, Trope, build_engine, build_session_factory, initialize_database
from app.db.init import _looks_like_missing_postgres_database


def test_initialize_database_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "schema.db"
    engine = build_engine(f"sqlite:///{db_path}")

    initialize_database(engine)

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "audit_events",
        "alembic_version",
        "datasets",
        "invite_reset_tokens",
        "jobs",
        "keywords",
        "review_items",
        "stories",
        "story_keywords",
        "story_tropes",
        "term_embeddings",
        "term_similarity_cache",
        "tropes",
        "user_sessions",
        "users",
    }

    with engine.connect() as connection:
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        alembic_version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    assert busy_timeout == 5000
    assert str(journal_mode).lower() == "wal"
    assert alembic_version == "20260715_0005"

    story_columns = {column["name"] for column in inspector.get_columns("stories")}
    assert "completeness" in story_columns


def test_story_source_row_number_unique_constraint_allows_multiple_nulls(tmp_path) -> None:
    db_path = tmp_path / "stories.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    SessionLocal = build_session_factory(engine)

    with SessionLocal() as session:
        dataset = Dataset(status=DatasetStatus.ACTIVE)
        session.add(dataset)
        session.commit()

        session.add_all(
            [
                Story(dataset_id=dataset.id, source_row_number=None, fields_json={}, row_hash=""),
                Story(dataset_id=dataset.id, source_row_number=None, fields_json={}, row_hash=""),
            ]
        )
        session.commit()


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


def test_trope_normalized_text_is_unique_within_a_dataset(tmp_path) -> None:
    db_path = tmp_path / "tropes.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    SessionLocal = build_session_factory(engine)

    with SessionLocal() as session:
        first_dataset = Dataset(status=DatasetStatus.ACTIVE)
        second_dataset = Dataset(status=DatasetStatus.STAGED)
        session.add_all([first_dataset, second_dataset])
        session.commit()

        session.add(Trope(dataset_id=first_dataset.id, text="Sky Woman"))
        session.commit()

        session.add(Trope(dataset_id=second_dataset.id, text="  sky\twoman  "))
        session.commit()

        session.add(Trope(dataset_id=first_dataset.id, text="  sky\twoman  "))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("Expected a unique constraint violation for duplicate normalized trope text.")


def test_missing_postgres_database_detection_matches_expected_error() -> None:
    error = OperationalError(
        "statement",
        {},
        Exception('connection failed: FATAL:  database "marawa" does not exist'),
    )

    assert _looks_like_missing_postgres_database(error) is True
