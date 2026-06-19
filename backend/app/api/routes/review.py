from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import ReviewStatus, UserRole
from app.services.auth import AuthSessionContext
from app.services.reviews import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewValidationError,
    approve_review_item,
    get_review_item_detail,
    list_review_items,
    reject_review_item,
)


class ReviewSubjectPreviewResponse(BaseModel):
    id: str
    title: str | None = None
    text: str | None = None
    source_row_number: int | None = None
    version: int | None = None
    review_status: str | None = None
    story_count: int | None = None


class ReviewItemResponse(BaseModel):
    id: str
    dataset_id: str | None
    review_type: str
    subject_table: str
    subject_id: str
    status: str
    created_by_user_id: str | None
    resolved_by_user_id: str | None
    created_at: str
    updated_at: str
    resolved_at: str | None
    metadata: dict
    subject_preview: ReviewSubjectPreviewResponse | None = None


class ApproveReviewRequest(BaseModel):
    note: str | None = None


class RejectReviewRequest(BaseModel):
    note: str | None = None
    merge_target_id: str | None = None
    remove_from_all_stories: bool = False


router = APIRouter(prefix="/review", tags=["review"])


def _raise_review_error(exc: Exception) -> None:
    if isinstance(exc, ReviewNotFoundError):
        raise api_error(404, "review_not_found", str(exc)) from exc
    if isinstance(exc, ReviewConflictError):
        raise api_error(409, "review_conflict", str(exc)) from exc
    if isinstance(exc, ReviewValidationError):
        raise api_error(400, "review_invalid", str(exc)) from exc
    raise exc


@router.get("/items", response_model=list[ReviewItemResponse])
def read_review_items(
    status: ReviewStatus | None = Query(default=ReviewStatus.PENDING),
    limit: int = Query(default=100, ge=1, le=500),
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[ReviewItemResponse]:
    return [
        ReviewItemResponse(**item)
        for item in list_review_items(session, status=status, limit=limit)
    ]


@router.get("/items/{review_id}", response_model=ReviewItemResponse)
def read_review_item(
    review_id: str,
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ReviewItemResponse:
    try:
        return ReviewItemResponse(**get_review_item_detail(session, review_id))
    except Exception as exc:
        _raise_review_error(exc)


@router.post("/items/{review_id}/approve", response_model=ReviewItemResponse)
def approve_review(
    review_id: str,
    payload: ApproveReviewRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ReviewItemResponse:
    try:
        return ReviewItemResponse(
            **approve_review_item(
                session,
                review_id=review_id,
                actor_user_id=auth_context.user.id,
                note=payload.note,
            )
        )
    except Exception as exc:
        _raise_review_error(exc)


@router.post("/items/{review_id}/reject", response_model=ReviewItemResponse)
def reject_review(
    review_id: str,
    payload: RejectReviewRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ReviewItemResponse:
    try:
        return ReviewItemResponse(
            **reject_review_item(
                session,
                review_id=review_id,
                actor_user_id=auth_context.user.id,
                note=payload.note,
                merge_target_id=payload.merge_target_id,
                remove_from_all_stories=payload.remove_from_all_stories,
            )
        )
    except Exception as exc:
        _raise_review_error(exc)
