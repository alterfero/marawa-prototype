from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.compute.job_runner import JobRunner
from app.db import Job, JobStatus, build_engine, build_session_factory, initialize_database
from app.main import create_app
from app.services.jobs import get_job, queue_job


def test_job_runner_transitions_job_from_queued_to_running_to_succeeded(tmp_path) -> None:
    db_path = tmp_path / "jobs-success.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    runner = JobRunner(session_factory)

    with session_factory() as session:
        job = queue_job(session, job_type="test_success", payload={"message": "hello"})
        session.commit()
        job_id = job.id

    claimed_job_id = runner.claim_next_job()
    assert claimed_job_id == job_id

    with session_factory() as session:
        running_job = get_job(session, job_id)
        assert running_job is not None
        assert running_job.status == JobStatus.RUNNING
        assert running_job.attempts == 1
        assert running_job.started_at is not None
        assert running_job.finished_at is None

    runner.execute_job(job_id)

    with session_factory() as session:
        completed_job = get_job(session, job_id)
        assert completed_job is not None
        assert completed_job.status == JobStatus.SUCCEEDED
        assert completed_job.finished_at is not None
        assert completed_job.result_json["message"] == "hello"


def test_job_runner_marks_failed_jobs_and_preserves_error_message(tmp_path) -> None:
    db_path = tmp_path / "jobs-failure.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    runner = JobRunner(session_factory)

    with session_factory() as session:
        job = queue_job(session, job_type="test_failure", payload={"message": "boom"})
        session.commit()
        job_id = job.id

    processed = runner.process_next_job()
    assert processed is True

    with session_factory() as session:
        failed_job = get_job(session, job_id)
        assert failed_job is not None
        assert failed_job.status == JobStatus.FAILED
        assert failed_job.finished_at is not None
        assert failed_job.error_message == "boom"


def test_rebuild_job_failure_is_persisted(tmp_path) -> None:
    db_path = tmp_path / "jobs-rebuild-failure.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    def failing_rebuild(_: object, __: Job) -> dict:
        raise RuntimeError("rebuild exploded")

    runner = JobRunner(session_factory, handlers={"full_rebuild": failing_rebuild})

    with session_factory() as session:
        job = queue_job(session, job_type="full_rebuild", payload={"reason": "test"})
        session.commit()
        job_id = job.id

    processed = runner.process_next_job()
    assert processed is True

    with session_factory() as session:
        failed_job = get_job(session, job_id)
        assert failed_job is not None
        assert failed_job.status == JobStatus.FAILED
        assert failed_job.error_message == "rebuild exploded"


def test_job_runner_coalesces_queued_rebuild_jobs_and_runs_only_latest(tmp_path) -> None:
    db_path = tmp_path / "jobs-coalesce.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    runner = JobRunner(session_factory)

    with session_factory() as session:
        older = queue_job(session, job_type="full_rebuild", payload={"label": "older"})
        newer = queue_job(session, job_type="full_rebuild", payload={"label": "newer"})
        session.commit()
        older_id = older.id
        newer_id = newer.id

    claimed_job_id = runner.claim_next_job()
    assert claimed_job_id == newer_id

    with session_factory() as session:
        older_job = get_job(session, older_id)
        newer_job = get_job(session, newer_id)
        assert older_job is not None
        assert newer_job is not None
        assert older_job.status == JobStatus.CANCELLED
        assert older_job.result_json["coalesced"] is True
        assert older_job.result_json["superseded_by_job_id"] == newer_id
        assert newer_job.status == JobStatus.RUNNING

    runner.execute_job(newer_id)

    with session_factory() as session:
        newer_job = get_job(session, newer_id)
        assert newer_job is not None
        assert newer_job.status == JobStatus.SUCCEEDED


def test_stale_running_jobs_are_requeued_on_app_startup(tmp_path) -> None:
    db_path = tmp_path / "jobs-recovery.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        job = Job(
            job_type="test_success",
            status=JobStatus.RUNNING,
            payload_json={"message": "recover"},
            result_json={},
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)
    with TestClient(app):
        pass

    with session_factory() as session:
        recovered_job = get_job(session, job_id)
        assert recovered_job is not None
        assert recovered_job.status == JobStatus.QUEUED
        assert recovered_job.started_at is None
        assert recovered_job.result_json["recovered_from_stale_running"] is True
