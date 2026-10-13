import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.compute.job_runner import JobRunner
from app.db import Dataset, DatasetStatus, JobStatus, build_engine, build_session_factory, initialize_database
from app.services.jobs import get_job, queue_job
from app.services.usage import get_usage_report, record_activity


POSTGRES_TEST_URL = os.getenv("MARAWA_POSTGRES_TEST_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="MARAWA_POSTGRES_TEST_URL is required for PostgreSQL smoke verification.",
)


def test_postgres_smoke_migrations_and_job_runner() -> None:
    engine = build_engine(POSTGRES_TEST_URL)
    initialize_database(engine)

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "audit_events",
        "alembic_version",
        "datasets",
        "dataset_snapshots",
        "invite_reset_tokens",
        "jobs",
        "keywords",
        "review_items",
        "stories",
        "story_keywords",
        "story_themes",
        "story_tropes",
        "term_embeddings",
        "term_similarity_cache",
        "themes",
        "tropes",
        "usage_activity",
        "user_sessions",
        "users",
    }

    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "20261013_0018"

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert {"territories", "version"}.issubset(user_columns)

    SessionLocal = build_session_factory(engine)

    with SessionLocal() as session:
        session.add(Dataset(status=DatasetStatus.ACTIVE))
        session.commit()

        session.add(Dataset(status=DatasetStatus.ACTIVE))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        job = queue_job(session, job_type="test_success", payload={"message": "hello-postgres"})
        session.commit()
        job_id = job.id

    runner = JobRunner(SessionLocal)
    processed = runner.process_next_job()
    assert processed is True

    with SessionLocal() as session:
        completed_job = get_job(session, job_id)
        assert completed_job is not None
        assert completed_job.status == JobStatus.SUCCEEDED
        assert completed_job.result_json["message"] == "hello-postgres"


def test_postgres_usage_concurrent_upserts_and_utc_reporting() -> None:
    engine = build_engine(POSTGRES_TEST_URL)
    initialize_database(engine)
    factory = build_session_factory(engine)
    hour = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)

    def ping(minute):
        with factory() as session:
            record_activity(session, browser_key="postgres-smoke-visitor", user=None, now=hour + timedelta(minutes=minute))

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(ping, [10, 20, 30, 40]))
    with factory() as session:
        report = get_usage_report(session, hour, hour + timedelta(hours=2))
    assert [entry["total_visitors"] for entry in report["hours"]] == [1, 0]
    assert report["hours"][0]["hour_start"] == "2026-10-13T10:00:00+00:00"
    engine.dispose()
