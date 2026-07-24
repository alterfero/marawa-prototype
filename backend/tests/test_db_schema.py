from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import Dataset, DatasetStatus, Story, Trope, build_engine, build_session_factory, initialize_database
from app.db.init import _build_alembic_config, _looks_like_missing_postgres_database


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
    assert alembic_version == "20260721_0006"

    story_columns = {column["name"] for column in inspector.get_columns("stories")}
    assert "completeness" in story_columns
    trope_columns = {column["name"] for column in inspector.get_columns("tropes")}
    assert "confirmation_status" in trope_columns
    assert "version" in trope_columns


def test_initialize_database_recovers_from_interrupted_sqlite_dataset_scope_upgrade(tmp_path) -> None:
    db_path = tmp_path / "recovery.db"
    engine = build_engine(f"sqlite:///{db_path}")
    config = _build_alembic_config(str(engine.url))

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "20260608_0001")

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE tropes_scoped (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE keywords_scoped (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE story_tropes_scoped (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE story_keywords_scoped (id TEXT PRIMARY KEY)")

    initialize_database(engine)

    inspector = inspect(engine)
    assert "tropes_scoped" not in inspector.get_table_names()
    assert "keywords_scoped" not in inspector.get_table_names()

    with engine.connect() as connection:
        alembic_version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    assert alembic_version == "20260721_0006"


def test_initialize_database_upgrades_populated_sqlite_db_with_term_and_story_foreign_keys(tmp_path) -> None:
    db_path = tmp_path / "populated-upgrade.db"
    engine = build_engine(f"sqlite:///{db_path}")
    config = _build_alembic_config(str(engine.url))

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "20260617_0003")

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO datasets (
                version, status, source_filename, activated_at, notes_json, id, created_at, updated_at
            ) VALUES (
                1, 'active', 'stories.csv', NULL, '{}', 'dataset-1', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO stories (
                dataset_id, source_row_number, fields_json, row_hash, version, id, created_at, updated_at
            ) VALUES (
                'dataset-1', 1, '{}', 'row-hash-1', 1, 'story-1', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO tropes (
                dataset_id, text, normalized_text, cached_story_count, id, created_at, updated_at
            ) VALUES (
                'dataset-1', 'Sky Woman', 'sky woman', 1, 'trope-1', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO keywords (
                dataset_id, text, normalized_text, cached_story_count, id, created_at, updated_at
            ) VALUES (
                'dataset-1', 'creation', 'creation', 1, 'keyword-1', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO story_tropes (
                story_id, trope_id, origin, status, position, created_at, updated_at
            ) VALUES (
                'story-1', 'trope-1', 'csv_import', 'validated', 0, '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO story_keywords (
                story_id, keyword_id, position, created_at, updated_at
            ) VALUES (
                'story-1', 'keyword-1', 0, '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )

    initialize_database(engine)

    inspector = inspect(engine)
    trope_columns = {column["name"] for column in inspector.get_columns("tropes")}
    story_columns = {column["name"] for column in inspector.get_columns("stories")}
    assert "review_status" in trope_columns
    assert "confirmation_status" in trope_columns
    assert "completeness" in story_columns

    with engine.connect() as connection:
        alembic_version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        trope_count = connection.exec_driver_sql("SELECT COUNT(*) FROM story_tropes").scalar_one()
        keyword_count = connection.exec_driver_sql("SELECT COUNT(*) FROM story_keywords").scalar_one()

    assert alembic_version == "20260721_0006"
    assert trope_count == 1
    assert keyword_count == 1


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
