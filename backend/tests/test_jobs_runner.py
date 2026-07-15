from datetime import datetime, timedelta, timezone
import threading
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.compute.job_runner import JobRunner
from app.db import Dataset, DatasetStatus, Job, JobStatus, build_engine, build_session_factory, initialize_database
from app.main import create_app
from app.services.jobs import get_job, queue_job, requeue_stale_running_jobs


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


def test_job_runner_coalesces_rebuild_jobs_within_the_same_dataset_only(tmp_path) -> None:
    db_path = tmp_path / "jobs-coalesce.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    runner = JobRunner(session_factory)

    with session_factory() as session:
        first_dataset = Dataset(status=DatasetStatus.ACTIVE)
        second_dataset = Dataset(status=DatasetStatus.STAGED)
        session.add_all([first_dataset, second_dataset])
        session.commit()
        older = queue_job(session, job_type="full_rebuild", dataset_id=first_dataset.id, payload={"label": "older"})
        newer = queue_job(session, job_type="full_rebuild", dataset_id=first_dataset.id, payload={"label": "newer"})
        other_dataset_job = queue_job(
            session,
            job_type="full_rebuild",
            dataset_id=second_dataset.id,
            payload={"label": "other-dataset"},
        )
        session.commit()
        older_id = older.id
        newer_id = newer.id
        other_dataset_job_id = other_dataset_job.id

    claimed_job_id = runner.claim_next_job()
    assert claimed_job_id == older_id

    with session_factory() as session:
        older_job = get_job(session, older_id)
        newer_job = get_job(session, newer_id)
        other_job = get_job(session, other_dataset_job_id)
        assert older_job is not None
        assert newer_job is not None
        assert other_job is not None
        assert older_job.status == JobStatus.RUNNING
        assert newer_job.status == JobStatus.CANCELLED
        assert newer_job.result_json["coalesced"] is True
        assert newer_job.result_json["superseded_by_job_id"] == older_id
        assert other_job.status == JobStatus.QUEUED

    runner.execute_job(older_id)

    with session_factory() as session:
        completed_job = get_job(session, older_id)
        remaining_other_job = get_job(session, other_dataset_job_id)
        assert completed_job is not None
        assert remaining_other_job is not None
        assert completed_job.status == JobStatus.SUCCEEDED
        assert remaining_other_job.status == JobStatus.QUEUED


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


def test_requeue_stale_running_jobs_respects_staleness_threshold(tmp_path) -> None:
    db_path = tmp_path / "jobs-threshold.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    now = datetime.now(timezone.utc)

    with session_factory() as session:
        stale_job = Job(
            job_type="test_success",
            status=JobStatus.RUNNING,
            payload_json={"message": "stale"},
            result_json={},
            started_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
        )
        fresh_job = Job(
            job_type="test_success",
            status=JobStatus.RUNNING,
            payload_json={"message": "fresh"},
            result_json={},
            started_at=now,
            updated_at=now,
        )
        session.add_all([stale_job, fresh_job])
        session.commit()
        stale_job_id = stale_job.id
        fresh_job_id = fresh_job.id

    with session_factory() as session:
        recovered = requeue_stale_running_jobs(session, stale_after_seconds=60, now=now)
        assert recovered == 1

    with session_factory() as session:
        recovered_stale_job = get_job(session, stale_job_id)
        untouched_fresh_job = get_job(session, fresh_job_id)
        assert recovered_stale_job is not None
        assert untouched_fresh_job is not None
        assert recovered_stale_job.status == JobStatus.QUEUED
        assert untouched_fresh_job.status == JobStatus.RUNNING


def test_job_runner_reclaims_stale_running_jobs_during_normal_processing(tmp_path) -> None:
    db_path = tmp_path / "jobs-runtime-recovery.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    now = datetime.now(timezone.utc)

    with session_factory() as session:
        job = Job(
            job_type="test_success",
            status=JobStatus.RUNNING,
            payload_json={"message": "recover"},
            result_json={},
            started_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    runner = JobRunner(session_factory, running_job_stale_after_seconds=60)
    processed = runner.process_next_job()
    assert processed is True

    with session_factory() as session:
        recovered_job = get_job(session, job_id)
        assert recovered_job is not None
        assert recovered_job.status == JobStatus.SUCCEEDED
        assert recovered_job.result_json["message"] == "recover"


def test_job_runner_heartbeat_updates_running_job_activity(tmp_path) -> None:
    db_path = tmp_path / "jobs-heartbeat.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    release_job = threading.Event()
    handler_started = threading.Event()

    def blocking_handler(_: object, job: Job) -> dict:
        handler_started.set()
        release_job.wait(timeout=5)
        return {"message": job.payload_json["message"]}

    runner = JobRunner(
        session_factory,
        handlers={"test_success": blocking_handler},
        heartbeat_interval_seconds=0.05,
        running_job_stale_after_seconds=1,
    )

    with session_factory() as session:
        job = queue_job(session, job_type="test_success", payload={"message": "heartbeat"})
        session.commit()
        job_id = job.id

    claimed_job_id = runner.claim_next_job()
    assert claimed_job_id == job_id

    worker = threading.Thread(target=runner.execute_job, args=(job_id,), daemon=True)
    worker.start()
    assert handler_started.wait(timeout=2)

    time.sleep(0.15)

    with session_factory() as session:
        running_job = get_job(session, job_id)
        assert running_job is not None
        assert running_job.status == JobStatus.RUNNING
        assert "heartbeat_at" in running_job.result_json

    release_job.set()
    worker.join(timeout=2)

    with session_factory() as session:
        completed_job = get_job(session, job_id)
        assert completed_job is not None
        assert completed_job.status == JobStatus.SUCCEEDED
        assert completed_job.result_json["message"] == "heartbeat"
