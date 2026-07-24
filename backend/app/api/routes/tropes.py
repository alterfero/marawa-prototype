from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import TropeConfirmationStatus, UserRole
from app.services.auth import AuthSessionContext
from app.services.curation import CurationConflictError, CurationNotFoundError, delete_trope, list_canonical_tropes
from app.services.tropes import (
    TropeLookupNotFoundError,
    TropeVersionConflictError,
    TropeMutationValidationError,
    ensure_canonical_trope,
    get_trope_detail,
    set_trope_confirmation_status,
    update_trope_text,
)


class JobSummaryResponse(BaseModel):
    id: str
    status: str
    job_type: str


class DeleteTropeResponse(BaseModel):
    deleted_trope_id: str
    affected_story_count: int
    dataset_version: int
    queued_job: JobSummaryResponse | None


class TropeListItemResponse(BaseModel):
    id: str
    version: int
    text: str
    confirmation_status: str
    story_count: int
    story_ids: list[str] = Field(default_factory=list)


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
    version: int
    text: str
    confirmation_status: str
    story_count: int
    stories: list[TropeStorySummaryResponse]


class UpdateTropeConfirmationRequest(BaseModel):
    expected_trope_version: int = Field(ge=1)
    confirmation_status: TropeConfirmationStatus


class UpdateTropeRequest(BaseModel):
    expected_trope_version: int = Field(ge=1)
    text: str = Field(min_length=1)


class UpdateTropeResponse(BaseModel):
    trope: TropeListItemResponse


class UpdateTropeConfirmationResponse(BaseModel):
    trope: TropeListItemResponse


router = APIRouter(prefix="/tropes", tags=["tropes"])


def _queued_job_summary(job) -> JobSummaryResponse | None:
    if job is None:
        return None
    return JobSummaryResponse(
        id=job.id,
        status=job.status.value,
        job_type=job.job_type,
    )


@router.get("", response_model=list[TropeListItemResponse])
def read_tropes(
    unused_only: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=5000),
    include_story_ids: bool = Query(default=False),
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> list[TropeListItemResponse]:
    return [
        TropeListItemResponse(**item)
        for item in list_canonical_tropes(
            session,
            unused_only=unused_only,
            query=q,
            limit=limit,
            include_story_ids=include_story_ids,
        )
    ]


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


@router.put("/{trope_id}", response_model=UpdateTropeResponse)
def update_canonical_trope(
    trope_id: str,
    payload: UpdateTropeRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> UpdateTropeResponse:
    try:
        trope = update_trope_text(
            session,
            trope_id,
            expected_version=payload.expected_trope_version,
            text=payload.text,
            actor_user_id=auth_context.user.id,
        )
    except TropeLookupNotFoundError as exc:
        raise api_error(404, "trope_not_found", str(exc)) from exc
    except TropeVersionConflictError as exc:
        raise api_error(409, "trope_version_conflict", str(exc)) from exc
    except TropeMutationValidationError as exc:
        raise api_error(400, "trope_mutation_invalid", str(exc)) from exc

    return UpdateTropeResponse(
        trope=TropeListItemResponse(**trope),
    )


@router.put("/{trope_id}/confirmation", response_model=UpdateTropeConfirmationResponse)
def update_trope_confirmation(
    trope_id: str,
    payload: UpdateTropeConfirmationRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> UpdateTropeConfirmationResponse:
    try:
        trope = set_trope_confirmation_status(
            session,
            trope_id,
            expected_version=payload.expected_trope_version,
            confirmation_status=payload.confirmation_status,
            actor_user_id=auth_context.user.id,
        )
    except TropeLookupNotFoundError as exc:
        raise api_error(404, "trope_not_found", str(exc)) from exc
    except TropeVersionConflictError as exc:
        raise api_error(409, "trope_version_conflict", str(exc)) from exc
    except TropeMutationValidationError as exc:
        raise api_error(400, "trope_mutation_invalid", str(exc)) from exc

    return UpdateTropeConfirmationResponse(
        trope=TropeListItemResponse(**trope),
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
