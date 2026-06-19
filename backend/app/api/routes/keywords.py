from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import UserRole
from app.services.auth import AuthSessionContext
from app.services.keywords import (
    KeywordLookupNotFoundError,
    KeywordMutationValidationError,
    ensure_canonical_keyword,
    get_keyword_detail,
    list_canonical_keywords,
)


class KeywordListItemResponse(BaseModel):
    id: str
    text: str
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


class KeywordDetailResponse(BaseModel):
    id: str
    text: str
    story_count: int
    stories: list[KeywordStorySummaryResponse]


router = APIRouter(prefix="/keywords", tags=["keywords"])


@router.get("", response_model=list[KeywordListItemResponse])
def read_keywords(
    unused_only: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
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
