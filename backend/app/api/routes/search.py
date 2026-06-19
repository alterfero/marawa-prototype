from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role
from app.db.models import TermKind, UserRole


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


class SearchExplanationResponse(BaseModel):
    method: str
    model_name: str
    artifact_version: int
    vector_dimension: int | None
    cache_hit: bool
    matched_query_exactly: bool
    near_duplicate: bool


class SearchItemResponse(BaseModel):
    id: str
    text: str
    story_count: int
    score: float
    explanation: SearchExplanationResponse


class SearchResponse(BaseModel):
    items: list[SearchItemResponse]
    model_name: str
    artifact_version: int | None


router = APIRouter(prefix="/search", tags=["search"])


def get_search_service(request: Request):
    return request.app.state.search_service


@router.post("/tropes", response_model=SearchResponse)
def search_tropes(
    payload: SearchRequest,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> SearchResponse:
    return SearchResponse(**search_service.search_terms(session, TermKind.TROPE, payload.query, limit=payload.limit))


@router.post("/keywords", response_model=SearchResponse)
def search_keywords(
    payload: SearchRequest,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> SearchResponse:
    return SearchResponse(**search_service.search_terms(session, TermKind.KEYWORD, payload.query, limit=payload.limit))
