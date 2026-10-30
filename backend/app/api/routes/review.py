from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
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
    list_review_stories,
    list_story_review_items,
    reject_review_item,
    resolve_story_data_review,
    resolve_story_term_review,
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


class ReviewEditRequest(BaseModel):
    text: str
    existing_term_id: str | None = None


class ReviewStoryResponse(BaseModel):
    story_id: str
    title: str
    source_row_number: int | None
    item_count: int
    pending_count: int
    latest_created_at: str


class ApproveReviewRequest(BaseModel):
    note: str | None = None
    edited: bool = False
    edit: ReviewEditRequest | None = None
    expected_story_version: int | None = None


class RejectReviewRequest(BaseModel):
    note: str | None = None
    merge_target_id: str | None = None
    remove_from_all_stories: bool = False


class StoryDataReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_story_version: int = Field(ge=1)
    review_ids: list[str] = Field(min_length=1)
    note: str | None = None


class ApproveStoryDataReviewRequest(StoryDataReviewRequest):
    fields: dict[str, str] = Field(default_factory=dict)


class StoryTermReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_story_version: int = Field(ge=1)
    edit: ReviewEditRequest | None = None
    existing_trope_ids: list[str] | None = Field(default=None, min_length=1)
    keep_proposed_trope: bool = False


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
    status: ReviewStatus | Literal["all"] | None = Query(default=ReviewStatus.PENDING),
    limit: int = Query(default=100, ge=1, le=500),
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[ReviewItemResponse]:
    return [
        ReviewItemResponse(**item)
        for item in list_review_items(session, status=None if status == "all" else status, limit=limit)
    ]


@router.get("/stories", response_model=list[ReviewStoryResponse])
def read_review_stories(
    status: ReviewStatus | Literal["all"] = Query(default=ReviewStatus.PENDING),
    limit: int = Query(default=100, ge=1, le=500),
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[ReviewStoryResponse]:
    return [ReviewStoryResponse(**story) for story in list_review_stories(
        session, status=None if status == "all" else status, limit=limit,
    )]


@router.get("/stories/{story_id}/items", response_model=list[ReviewItemResponse])
def read_story_review_items(
    story_id: str,
    status: ReviewStatus | Literal["all"] = Query(default=ReviewStatus.PENDING),
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[ReviewItemResponse]:
    return [ReviewItemResponse(**item) for item in list_story_review_items(
        session, story_id, status=None if status == "all" else status,
    )]


@router.post("/stories/{story_id}/approve", response_model=list[ReviewItemResponse])
def approve_story_data_review(
    story_id: str,
    payload: ApproveStoryDataReviewRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[ReviewItemResponse]:
    try:
        return [ReviewItemResponse(**item) for item in resolve_story_data_review(
            session, story_id=story_id, actor_user_id=auth_context.user.id,
            approve=True, **payload.model_dump(),
        )]
    except Exception as exc:
        _raise_review_error(exc)


@router.post("/stories/{story_id}/reject", response_model=list[ReviewItemResponse])
def reject_story_data_review(
    story_id: str,
    payload: StoryDataReviewRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[ReviewItemResponse]:
    try:
        return [ReviewItemResponse(**item) for item in resolve_story_data_review(
            session, story_id=story_id, actor_user_id=auth_context.user.id,
            approve=False, **payload.model_dump(),
        )]
    except Exception as exc:
        _raise_review_error(exc)


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


@router.post("/stories/{story_id}/items/{review_id}/{decision}", response_model=ReviewItemResponse)
def decide_story_term_review(
    story_id: str,
    review_id: str,
    decision: Literal["approve", "reject"],
    payload: StoryTermReviewRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ReviewItemResponse:
    try:
        return ReviewItemResponse(**resolve_story_term_review(
            session, story_id=story_id, review_id=review_id, actor_user_id=auth_context.user.id,
            approve=decision == "approve", **payload.model_dump(),
        ))
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
                edited=payload.edited,
                edit=payload.edit.model_dump() if payload.edit else None,
                expected_story_version=payload.expected_story_version,
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
