from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import KeywordConfirmationStatus, UserRole
from app.services.auth import AuthSessionContext
from app.services.keywords import (
    KeywordLookupNotFoundError,
    KeywordDeletionConflictError,
    KeywordMutationValidationError,
    KeywordVersionConflictError,
    delete_keyword,
    ensure_canonical_keyword,
    get_keyword_detail,
    list_canonical_keywords,
    merge_unconfirmed_keyword,
    set_keyword_confirmation_status,
    update_keyword_text,
)


class KeywordListItemResponse(BaseModel):
    id: str
    version: int
    text: str
    confirmation_status: KeywordConfirmationStatus
    story_count: int


class CreateKeywordRequest(BaseModel):
    text: str = Field(min_length=1)


class CreateKeywordResponse(BaseModel):
    keyword: KeywordListItemResponse
    created: bool


class KeywordStorySummaryResponse(BaseModel):
    id: str
    title: str
    source_row_number: int | None


class KeywordDetailResponse(KeywordListItemResponse):
    stories: list[KeywordStorySummaryResponse]


class UpdateKeywordRequest(BaseModel):
    expected_keyword_version: int = Field(ge=1)
    text: str = Field(min_length=1)


class UpdateKeywordConfirmationRequest(BaseModel):
    expected_keyword_version: int = Field(ge=1)
    confirmation_status: KeywordConfirmationStatus


class UpdateKeywordResponse(BaseModel):
    keyword: KeywordListItemResponse


class UpdateKeywordConfirmationResponse(BaseModel):
    keyword: KeywordListItemResponse


class DeleteKeywordResponse(BaseModel):
    deleted_keyword_id: str
    affected_story_count: int
    dataset_version: int


class MergeKeywordRequest(BaseModel):
    expected_source_keyword_version: int = Field(ge=1)
    target_keyword_id: str


class MergeKeywordResponse(BaseModel):
    source_keyword_id: str
    target_keyword: KeywordListItemResponse
    affected_story_count: int
    dataset_version: int


router = APIRouter(prefix="/keywords", tags=["keywords"])


@router.get("", response_model=list[KeywordListItemResponse])
def read_keywords(
    unused_only: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=5000),
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> list[KeywordListItemResponse]:
    return [
        KeywordListItemResponse(**item)
        for item in list_canonical_keywords(session, unused_only=unused_only, query=q, limit=limit)
    ]


@router.get("/{keyword_id}", response_model=KeywordDetailResponse)
def read_keyword_detail(
    keyword_id: str,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> KeywordDetailResponse:
    try:
        return KeywordDetailResponse(**get_keyword_detail(session, keyword_id))
    except KeywordLookupNotFoundError as exc:
        raise api_error(404, "keyword_not_found", str(exc)) from exc


@router.post("", response_model=CreateKeywordResponse)
def create_canonical_keyword(
    payload: CreateKeywordRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> CreateKeywordResponse:
    try:
        keyword, created = ensure_canonical_keyword(
            session,
            payload.text,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except KeywordMutationValidationError as exc:
        raise api_error(400, "keyword_mutation_invalid", str(exc)) from exc

    return CreateKeywordResponse(
        keyword=KeywordListItemResponse(**keyword),
        created=created,
    )


@router.put("/{keyword_id}", response_model=UpdateKeywordResponse)
def update_canonical_keyword(
    keyword_id: str,
    payload: UpdateKeywordRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> UpdateKeywordResponse:
    try:
        keyword = update_keyword_text(
            session,
            keyword_id,
            expected_version=payload.expected_keyword_version,
            text=payload.text,
            actor_user_id=auth_context.user.id,
        )
    except KeywordLookupNotFoundError as exc:
        raise api_error(404, "keyword_not_found", str(exc)) from exc
    except KeywordVersionConflictError as exc:
        raise api_error(409, "keyword_version_conflict", str(exc)) from exc
    except KeywordMutationValidationError as exc:
        raise api_error(400, "keyword_mutation_invalid", str(exc)) from exc
    return UpdateKeywordResponse(keyword=KeywordListItemResponse(**keyword))


@router.put("/{keyword_id}/confirmation", response_model=UpdateKeywordConfirmationResponse)
def update_keyword_confirmation(
    keyword_id: str,
    payload: UpdateKeywordConfirmationRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> UpdateKeywordConfirmationResponse:
    try:
        keyword = set_keyword_confirmation_status(
            session,
            keyword_id,
            expected_version=payload.expected_keyword_version,
            confirmation_status=payload.confirmation_status,
            actor_user_id=auth_context.user.id,
        )
    except KeywordLookupNotFoundError as exc:
        raise api_error(404, "keyword_not_found", str(exc)) from exc
    except KeywordVersionConflictError as exc:
        raise api_error(409, "keyword_version_conflict", str(exc)) from exc
    except KeywordMutationValidationError as exc:
        raise api_error(400, "keyword_mutation_invalid", str(exc)) from exc
    return UpdateKeywordConfirmationResponse(keyword=KeywordListItemResponse(**keyword))


@router.post("/{keyword_id}/merge", response_model=MergeKeywordResponse)
def merge_keyword(
    keyword_id: str,
    payload: MergeKeywordRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> MergeKeywordResponse:
    try:
        dataset, summary = merge_unconfirmed_keyword(
            session,
            keyword_id,
            target_keyword_id=payload.target_keyword_id,
            expected_source_version=payload.expected_source_keyword_version,
            actor_user_id=auth_context.user.id,
        )
    except KeywordLookupNotFoundError as exc:
        raise api_error(404, "keyword_not_found", str(exc)) from exc
    except KeywordVersionConflictError as exc:
        raise api_error(409, "keyword_version_conflict", str(exc)) from exc
    except KeywordMutationValidationError as exc:
        raise api_error(400, "keyword_merge_invalid", str(exc)) from exc
    return MergeKeywordResponse(
        source_keyword_id=summary["source_keyword_id"],
        target_keyword=KeywordListItemResponse(**summary["target_keyword"]),
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
    )


@router.delete("/{keyword_id}", response_model=DeleteKeywordResponse)
def remove_canonical_keyword(
    keyword_id: str,
    expected_keyword_version: int = Query(ge=1),
    remove_from_all_stories: bool = Query(default=False),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> DeleteKeywordResponse:
    try:
        dataset, summary = delete_keyword(
            session,
            keyword_id,
            expected_version=expected_keyword_version,
            remove_from_all_stories=remove_from_all_stories,
            actor_user_id=auth_context.user.id,
        )
    except KeywordLookupNotFoundError as exc:
        raise api_error(404, "keyword_not_found", str(exc)) from exc
    except KeywordVersionConflictError as exc:
        raise api_error(409, "keyword_version_conflict", str(exc)) from exc
    except KeywordDeletionConflictError as exc:
        raise api_error(409, "keyword_delete_conflict", str(exc)) from exc
    except KeywordMutationValidationError as exc:
        raise api_error(400, "keyword_mutation_invalid", str(exc)) from exc
    return DeleteKeywordResponse(
        deleted_keyword_id=summary["deleted_keyword_id"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
    )
