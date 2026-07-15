from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def queue_job(
    session: Session,
    *,
    job_type: str,
    dataset_id: str | None = None,
    payload: dict | None = None,
) -> Job:
    job = Job(
        dataset_id=dataset_id,
        job_type=job_type,
        status=JobStatus.QUEUED,
        payload_json=payload or {},
        result_json={},
    )
    session.add(job)
    session.flush()
    return job


def list_jobs(session: Session, *, limit: int = 20) -> list[Job]:
    return list(session.scalars(select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)).all())


def get_job(session: Session, job_id: str) -> Job | None:
    return session.get(Job, job_id)


def _job_last_activity_at(job: Job) -> datetime:
    return _coerce_utc(job.updated_at or job.started_at or job.created_at)


def requeue_stale_running_jobs(
    session: Session,
    *,
    stale_after_seconds: float | None = None,
    now: datetime | None = None,
) -> int:
    stale_jobs = list(session.scalars(select(Job).where(Job.status == JobStatus.RUNNING)).all())
    if stale_after_seconds is not None:
        cutoff = (now or utc_now()) - timedelta(seconds=stale_after_seconds)
        stale_jobs = [job for job in stale_jobs if _job_last_activity_at(job) <= cutoff]
    for job in stale_jobs:
        job.status = JobStatus.QUEUED
        job.started_at = None
        job.finished_at = None
        job.error_message = None
        job.result_json = {
            **(job.result_json or {}),
            "recovered_from_stale_running": True,
        }
    session.commit()
    return len(stale_jobs)
