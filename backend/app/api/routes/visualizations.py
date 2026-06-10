from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.api.errors import api_error
from app.services.visualizations import (
    VisualizationNotFoundError,
    VisualizationValidationError,
    build_trope_sequence_graph,
)


class TropeSequenceGraphRequest(BaseModel):
    query: str | None = None
    selected_trope_id: str | None = None
    similarity_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    max_stories: int = Field(default=150, ge=1, le=500)
    max_links_per_node: int = Field(default=4, ge=0, le=12)
    vertical_spacing: float = Field(default=30, gt=0, le=240)


class TropeSequenceGraphSelectedTropeResponse(BaseModel):
    id: str
    text: str
    score: float


class TropeSequenceGraphLayoutBasisResponse(BaseModel):
    selected_trope: TropeSequenceGraphSelectedTropeResponse
    query: str | None
    similarity_threshold: float
    max_stories: int
    max_links_per_node: int
    sequence_axis_label: str


class TropeSequenceGraphNodeResponse(BaseModel):
    id: str
    kind: str
    story_id: str
    story_title: str
    source_row_number: int | None = None
    story_match_score: float | None = None
    occurrence_count: int | None = None
    trope_id: str | None = None
    trope_text: str | None = None
    sequence_index: int | None = None
    anchor_x: float | None = None
    anchor_y: float | None = None
    target_z: float | None = None
    lat: float
    lon: float
    x: float
    y: float
    z: float
    fx: float | None = None
    fy: float | None = None
    fz: float | None = None
    has_location: bool
    status: str | None = None
    origin: str | None = None
    is_selected_trope: bool | None = None
    selected_similarity_score: float | None = None


class TropeSequenceGraphLinkResponse(BaseModel):
    source: str
    target: str
    kind: str
    strength: float
    similarity: float | None = None


class TropeSequenceGraphResponse(BaseModel):
    layout_basis: TropeSequenceGraphLayoutBasisResponse
    nodes: list[TropeSequenceGraphNodeResponse]
    links: list[TropeSequenceGraphLinkResponse]
    warnings: list[str]


router = APIRouter(prefix="/visualizations", tags=["visualizations"])


def get_search_service(request: Request):
    return request.app.state.search_service


@router.post("/trope-sequence-graph", response_model=TropeSequenceGraphResponse)
def build_trope_sequence_graph_endpoint(
    payload: TropeSequenceGraphRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> TropeSequenceGraphResponse:
    try:
        return TropeSequenceGraphResponse(
            **build_trope_sequence_graph(
                session,
                search_service,
                selected_trope_id=payload.selected_trope_id,
                query=payload.query,
                similarity_threshold=payload.similarity_threshold,
                max_stories=payload.max_stories,
                max_links_per_node=payload.max_links_per_node,
                vertical_spacing=payload.vertical_spacing,
            )
        )
    except VisualizationValidationError as exc:
        raise api_error(400, "visualization_invalid", str(exc)) from exc
    except VisualizationNotFoundError as exc:
        raise api_error(404, "visualization_not_found", str(exc)) from exc
