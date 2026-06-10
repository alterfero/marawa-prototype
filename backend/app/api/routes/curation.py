from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.deps import get_db_session
from app.services.curation import (
    CurationConflictError,
    CurationNotFoundError,
    CurationValidationError,
    list_near_duplicate_tropes,
    merge_tropes,
)


class JobSummaryResponse(BaseModel):
    id: str
    status: str
    job_type: str


class TropeSummaryResponse(BaseModel):
    id: str
    text: str
    story_count: int


class NearDuplicateTropePairResponse(BaseModel):
    source_trope: TropeSummaryResponse
    target_trope: TropeSummaryResponse
    similarity_score: float
    metadata: dict


class NearDuplicateTropeListResponse(BaseModel):
    items: list[NearDuplicateTropePairResponse]
    artifact_version: int | None
    model_name: str
    total: int


class MergeTropesRequest(BaseModel):
    source_trope_id: str
    target_trope_id: str


class MergeTropesResponse(BaseModel):
    source_trope_id: str
    target_trope_id: str
    affected_story_count: int
    dataset_version: int
    queued_job: JobSummaryResponse


router = APIRouter(prefix="/curation", tags=["curation"])


def get_search_service(request: Request):
    return request.app.state.search_service


def _queued_job_summary(job) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=job.id,
        status=job.status.value,
        job_type=job.job_type,
    )


def _raise_curation_error(exc: Exception) -> None:
    if isinstance(exc, CurationNotFoundError):
        raise api_error(404, "trope_not_found", str(exc)) from exc
    if isinstance(exc, CurationConflictError):
        raise api_error(409, "trope_merge_conflict", str(exc)) from exc
    if isinstance(exc, CurationValidationError):
        raise api_error(400, "trope_merge_invalid", str(exc)) from exc
    raise exc


@router.get("/near-duplicate-tropes", response_model=NearDuplicateTropeListResponse)
def read_near_duplicate_tropes(
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> NearDuplicateTropeListResponse:
    return NearDuplicateTropeListResponse(
        **list_near_duplicate_tropes(session, model_name=search_service.model_name)
    )


@router.post("/merge-tropes", response_model=MergeTropesResponse)
def merge_canonical_tropes(
    payload: MergeTropesRequest,
    session: Session = Depends(get_db_session),
) -> MergeTropesResponse:
    try:
        dataset, summary, job = merge_tropes(
            session,
            source_trope_id=payload.source_trope_id,
            target_trope_id=payload.target_trope_id,
        )
    except Exception as exc:
        _raise_curation_error(exc)

    return MergeTropesResponse(
        source_trope_id=summary["source_trope_id"],
        target_trope_id=summary["target_trope_id"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )
