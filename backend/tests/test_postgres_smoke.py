import os

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.compute.job_runner import JobRunner
from app.db import Dataset, DatasetStatus, JobStatus, build_engine, build_session_factory, initialize_database
from app.services.jobs import get_job, queue_job


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
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "20260617_0004"

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
