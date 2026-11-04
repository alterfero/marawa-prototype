from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.api.deps import require_minimum_role
from app.api.errors import api_error
from app.db.models import UserRole
from app.services.tmi import get_tmi_catalogue


class TmiNodeResponse(BaseModel):
    id: str
    code: str
    text: str
    kind: Literal["trope", "chapter", "division", "heading", "gap"]
    parent_id: str | None
    chapter: str
    link_basis: Literal["code", "source_structure"]
    source_rows: list[int]
    source_texts: list[str]
    alternate_texts: list[str]
    bibliographies: list[str]
    edition: Literal["first", "revised"] | None
    first_edition_code: str | None


class TmiGraphResponse(BaseModel):
    nodes: list[TmiNodeResponse]
    roots: list[str]
    stats: dict[str, int]
    source: dict


router = APIRouter(prefix="/tmi", tags=["tmi"])


@router.get("/graph", response_model=TmiGraphResponse)
def get_tmi_graph_endpoint(response: Response, _: object = Depends(require_minimum_role(UserRole.ADMIN))):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return get_tmi_catalogue()
    except (OSError, ValueError, KeyError) as exc:
        raise api_error(503, "tmi_unavailable", "The TMI reference catalogue could not be loaded.") from exc
