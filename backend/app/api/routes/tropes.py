from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import UserRole
from app.services.auth import AuthSessionContext
from app.services.curation import CurationConflictError, CurationNotFoundError, delete_trope, list_canonical_tropes
from app.services.tropes import (
    TropeLookupNotFoundError,
    TropeMutationValidationError,
    ensure_canonical_trope,
    get_trope_detail,
)


class JobSummaryResponse(BaseModel):
    id: str
    status: str
    job_type: str


class DeleteTropeResponse(BaseModel):
    deleted_trope_id: str
    affected_story_count: int
    dataset_version: int
    queued_job: JobSummaryResponse


class TropeListItemResponse(BaseModel):
    id: str
    text: str
    story_count: int


class CreateTropeRequest(BaseModel):
    text: str = Field(min_length=1)


class CreateTropeResponse(BaseModel):
    trope: TropeListItemResponse
    created: bool


class TropeStorySummaryResponse(BaseModel):
    id: str
    title: str
    source_row_number: int | None


class TropeDetailResponse(BaseModel):
    id: str
    text: str
    story_count: int
    stories: list[TropeStorySummaryResponse]


router = APIRouter(prefix="/tropes", tags=["tropes"])


def _queued_job_summary(job) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=job.id,
        status=job.status.value,
        job_type=job.job_type,
    )


@router.get("", response_model=list[TropeListItemResponse])
def read_tropes(
    unused_only: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> list[TropeListItemResponse]:
    return [TropeListItemResponse(**item) for item in list_canonical_tropes(session, unused_only=unused_only, query=q, limit=limit)]


@router.get("/{trope_id}", response_model=TropeDetailResponse)
def read_trope_detail(
    trope_id: str,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> TropeDetailResponse:
    try:
        return TropeDetailResponse(**get_trope_detail(session, trope_id))
    except TropeLookupNotFoundError as exc:
        raise api_error(404, "trope_not_found", str(exc)) from exc


@router.post("", response_model=CreateTropeResponse)
def create_canonical_trope(
    payload: CreateTropeRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> CreateTropeResponse:
    try:
        trope, created = ensure_canonical_trope(
            session,
            payload.text,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except TropeMutationValidationError as exc:
        raise api_error(400, "trope_mutation_invalid", str(exc)) from exc

    return CreateTropeResponse(
        trope=TropeListItemResponse(**trope),
        created=created,
    )


@router.delete("/{trope_id}", response_model=DeleteTropeResponse)
def remove_canonical_trope(
    trope_id: str,
    remove_from_all_stories: bool = Query(default=False),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> DeleteTropeResponse:
    try:
        dataset, summary, job = delete_trope(
            session,
            trope_id=trope_id,
            remove_from_all_stories=remove_from_all_stories,
            actor_user_id=auth_context.user.id,
        )
    except CurationNotFoundError as exc:
        raise api_error(404, "trope_not_found", str(exc)) from exc
    except CurationConflictError as exc:
        raise api_error(409, "trope_delete_conflict", str(exc)) from exc

    return DeleteTropeResponse(
        deleted_trope_id=summary["deleted_trope_id"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )
