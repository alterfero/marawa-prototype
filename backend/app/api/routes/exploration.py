from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.deps import get_db_session
from app.services.exploration import (
    ExplorationNotFoundError,
    ExplorationValidationError,
    build_exploration_response,
)


class TropeCandidateResponse(BaseModel):
    id: str
    text: str
    story_count: int
    score: float


class SelectedTropeResponse(BaseModel):
    id: str
    text: str
    story_count: int


class MarkerMatchedTropeResponse(BaseModel):
    id: str
    text: str
    score: float
    story_count: int


class MarkerStoryTropeResponse(BaseModel):
    id: str
    text: str
    story_count: int


class ExplorationMarkerResponse(BaseModel):
    story_id: str
    source_row_number: int | None
    coordinates: list[float] | None
    kind: str
    similarity: float
    matched_tropes: list[MarkerMatchedTropeResponse]
    story_tropes: list[MarkerStoryTropeResponse]
    color: str
    title: str
    hover_title: str
    abstract: str
    has_location: bool


class ExplorationConnectionResponse(BaseModel):
    source_story_id: str
    target_story_id: str
    source_coordinates: list[float]
    target_coordinates: list[float]
    similarity: float
    color: str


class ExplorationNetworkRequest(BaseModel):
    selected_trope_id: str | None = None
    query: str | None = None
    min_similarity: float = Field(default=0.62, ge=0.0, le=1.0)
    related_limit: int = Field(default=60, ge=1, le=200)
    candidate_limit: int = Field(default=12, ge=1, le=50)


class ExplorationNetworkResponse(BaseModel):
    selected_trope: SelectedTropeResponse | None
    selected_trope_candidates: list[TropeCandidateResponse]
    related_tropes: list[TropeCandidateResponse]
    original_markers: list[ExplorationMarkerResponse]
    related_markers: list[ExplorationMarkerResponse]
    connections: list[ExplorationConnectionResponse]
    bounds: list[list[float]] | None
    missing_original_coords: int
    missing_related_coords: int


router = APIRouter(prefix="/exploration", tags=["exploration"])


def get_search_service(request: Request):
    return request.app.state.search_service


@router.post("/network", response_model=ExplorationNetworkResponse)
def build_exploration_network(
    payload: ExplorationNetworkRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> ExplorationNetworkResponse:
    try:
        return ExplorationNetworkResponse(
            **build_exploration_response(
                session,
                search_service,
                selected_trope_id=payload.selected_trope_id,
                query=payload.query,
                min_similarity=payload.min_similarity,
                related_limit=payload.related_limit,
                candidate_limit=payload.candidate_limit,
            )
        )
    except ExplorationValidationError as exc:
        raise api_error(400, "exploration_invalid", str(exc)) from exc
    except ExplorationNotFoundError as exc:
        raise api_error(404, "exploration_not_found", str(exc)) from exc
