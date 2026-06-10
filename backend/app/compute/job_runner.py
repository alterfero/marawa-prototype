from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job, JobStatus


JobHandler = Callable[[Session, Job], dict[str, Any] | None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def handle_full_rebuild_placeholder(_: Session, job: Job) -> dict[str, Any]:
    return {
        "message": "Placeholder rebuild completed without embedding work.",
        "dataset_id": job.dataset_id,
    }


def handle_test_success(_: Session, job: Job) -> dict[str, Any]:
    payload = job.payload_json or {}
    return {
        "message": payload.get("message", "test success"),
        "echo": payload,
    }


def handle_test_failure(_: Session, job: Job) -> dict[str, Any]:
    payload = job.payload_json or {}
    raise RuntimeError(payload.get("message", "test failure"))


class JobRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        handlers: dict[str, JobHandler] | None = None,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        self.session_factory = session_factory
        self.poll_interval_seconds = poll_interval_seconds
        self.handlers: dict[str, JobHandler] = {
            "full_rebuild": handle_full_rebuild_placeholder,
            "test_success": handle_test_success,
            "test_failure": handle_test_failure,
        }
        if handlers:
            self.handlers.update(handlers)
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            processed = await asyncio.to_thread(self.process_next_job)
            if not processed:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
                except asyncio.TimeoutError:
                    continue

    def claim_next_job(self) -> str | None:
        with self.session_factory() as session:
            queued_rebuilds = list(
                session.scalars(
                    select(Job)
                    .where(Job.status == JobStatus.QUEUED, Job.job_type == "full_rebuild")
                    .order_by(Job.created_at.desc(), Job.id.desc())
                ).all()
            )
            if queued_rebuilds:
                selected_job = queued_rebuilds[0]
                for stale_job in queued_rebuilds[1:]:
                    stale_job.status = JobStatus.CANCELLED
                    stale_job.finished_at = utc_now()
                    stale_job.result_json = {
                        **(stale_job.result_json or {}),
                        "coalesced": True,
                        "superseded_by_job_id": selected_job.id,
                    }
                selected_job.status = JobStatus.RUNNING
                selected_job.started_at = utc_now()
                selected_job.finished_at = None
                selected_job.error_message = None
                selected_job.attempts += 1
                session.commit()
                return selected_job.id

            next_job = session.scalar(
                select(Job)
                .where(Job.status == JobStatus.QUEUED)
                .order_by(Job.created_at.asc(), Job.id.asc())
            )
            if next_job is None:
                return None

            next_job.status = JobStatus.RUNNING
            next_job.started_at = utc_now()
            next_job.finished_at = None
            next_job.error_message = None
            next_job.attempts += 1
            session.commit()
            return next_job.id

    def execute_job(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            handler = self.handlers.get(job.job_type)
            if handler is None:
                job.status = JobStatus.FAILED
                job.error_message = f"No handler registered for job type `{job.job_type}`."
                job.finished_at = utc_now()
                session.commit()
                return

            try:
                result = handler(session, job) or {}
            except Exception as exc:
                session.rollback()
                job = session.get(Job, job_id)
                if job is None:
                    return
                job.status = JobStatus.FAILED
                job.error_message = str(exc)
                job.finished_at = utc_now()
                session.commit()
                return

            job.status = JobStatus.SUCCEEDED
            job.result_json = result
            job.error_message = None
            job.finished_at = utc_now()
            session.commit()

    def process_next_job(self) -> bool:
        job_id = self.claim_next_job()
        if job_id is None:
            return False
        self.execute_job(job_id)
        return True
