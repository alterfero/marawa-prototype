from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.deps import get_db_session
from app.services.jobs import get_job, list_jobs


class JobResponse(BaseModel):
    id: str
    dataset_id: str | None
    job_type: str
    status: str
    attempts: int
    payload: dict
    result: dict
    started_at: str | None
    finished_at: str | None
    error_message: str | None


def _serialize_job(job) -> JobResponse:
    return JobResponse(
        id=job.id,
        dataset_id=job.dataset_id,
        job_type=job.job_type,
        status=job.status.value,
        attempts=job.attempts,
        payload=job.payload_json or {},
        result=job.result_json or {},
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        error_message=job.error_message,
    )


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobResponse])
def read_jobs(
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> list[JobResponse]:
    return [_serialize_job(job) for job in list_jobs(session, limit=limit)]


@router.get("/{job_id}", response_model=JobResponse)
def read_job(job_id: str, session: Session = Depends(get_db_session)) -> JobResponse:
    job = get_job(session, job_id)
    if job is None:
        raise api_error(404, "job_not_found", "Job not found.")
    return _serialize_job(job)
