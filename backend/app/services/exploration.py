from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.coordinates import parse_space_coord
from app.core.parsing import clean_text
from app.db.models import Dataset, DatasetStatus, Story, StoryTrope, TermKind, Trope


TITLE_FIELDS = [
    "Story title (Eng)",
    "Story title (French)",
    "Story title (other)",
]

ABSTRACT_FIELDS = [
    "1-sentence summary",
    "Abstract (Eng)",
    "Abstract (Fr)",
]

RED_RGB = (215, 38, 61)
BLUE_RGB = (44, 123, 182)


@dataclass
class StoryEntry:
    story_id: str
    source_row_number: int | None
    fields: dict[str, str]
    tropes: list[dict]


class ExplorationError(ValueError):
    """Base error for exploration operations."""


class ExplorationNotFoundError(ExplorationError):
    """Raised when an exploration resource is missing."""


class ExplorationValidationError(ExplorationError):
    """Raised when the exploration request is invalid."""


def entry_title(entry: StoryEntry) -> str:
    for field_name in TITLE_FIELDS:
        title = clean_text(entry.fields.get(field_name, ""))
        if title:
            return title
    return entry.story_id


def english_story_title(entry: StoryEntry) -> str:
    english_title = clean_text(entry.fields.get("Story title (Eng)", ""))
    if english_title:
        return english_title
    return entry_title(entry)


def primary_abstract(entry: StoryEntry) -> str:
    preferred_fields = ["Abstract (Eng)", "Abstract (Fr)", "1-sentence summary"]
    for field_name in preferred_fields:
        value = clean_text(entry.fields.get(field_name, ""))
        if value:
            return value
    for field_name in ABSTRACT_FIELDS:
        value = clean_text(entry.fields.get(field_name, ""))
        if value:
            return value
    return ""


def similarity_to_color(score: float, minimum: float) -> str:
    if score >= 0.999:
        return f"#{RED_RGB[0]:02x}{RED_RGB[1]:02x}{RED_RGB[2]:02x}"

    span = max(1.0 - minimum, 1e-9)
    ratio = min(1.0, max(0.0, (score - minimum) / span))
    red = round(BLUE_RGB[0] + (RED_RGB[0] - BLUE_RGB[0]) * ratio)
    green = round(BLUE_RGB[1] + (RED_RGB[1] - BLUE_RGB[1]) * ratio)
    blue = round(BLUE_RGB[2] + (RED_RGB[2] - BLUE_RGB[2]) * ratio)
    return f"#{red:02x}{green:02x}{blue:02x}"


def build_exploration_response(
    session: Session,
    search_service,
    *,
    selected_trope_id: str | None,
    query: str | None,
    min_similarity: float = 0.62,
    related_limit: int = 60,
    candidate_limit: int = 12,
) -> dict:
    if selected_trope_id is None:
        if not clean_text(query):
            raise ExplorationValidationError("Provide selected_trope_id or a query to search for trope candidates.")
        candidates = search_service.search_terms(session, TermKind.TROPE, clean_text(query), limit=candidate_limit)
        return {
            "selected_trope": None,
            "selected_trope_candidates": [_serialize_candidate(item) for item in candidates["items"]],
            "related_tropes": [],
            "original_markers": [],
            "related_markers": [],
            "connections": [],
            "bounds": None,
            "missing_original_coords": 0,
            "missing_related_coords": 0,
        }

    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise ExplorationNotFoundError("No active dataset is available for exploration.")

    selected_trope = _get_active_trope(session, active_dataset.id, selected_trope_id)
    if selected_trope is None:
        raise ExplorationNotFoundError("Selected trope not found in the active dataset.")

    stories = _load_active_story_entries(session, active_dataset.id)
    stories_by_trope_id: dict[str, list[StoryEntry]] = {}
    for entry in stories:
        for trope in entry.tropes:
            stories_by_trope_id.setdefault(trope["id"], []).append(entry)
    story_counts_by_trope_id = {
        trope_id: len(entries)
        for trope_id, entries in stories_by_trope_id.items()
    }

    selected_story_ids = {
        entry.story_id
        for entry in stories_by_trope_id.get(selected_trope.id, [])
    }

    related_search = search_service.search_terms(session, TermKind.TROPE, selected_trope.text, limit=related_limit + 1)
    related_tropes = []
    for item in related_search["items"]:
        if item["id"] == selected_trope.id:
            continue
        if float(item["score"]) < min_similarity:
            continue
        related_tropes.append(_serialize_candidate(item))
        if len(related_tropes) >= related_limit:
            break

    original_markers: list[dict] = []
    missing_original_coords = 0
    for entry in stories_by_trope_id.get(selected_trope.id, []):
        coordinates = parse_space_coord(entry.fields.get("space coord", ""))
        has_location = coordinates is not None
        if not has_location:
            missing_original_coords += 1
        original_markers.append(
            _marker_payload(
                entry=entry,
                coordinates=coordinates,
                kind="original",
                similarity=1.0,
                matched_tropes=[
                    {
                        "id": selected_trope.id,
                        "text": selected_trope.text,
                        "score": 1.0,
                        "story_count": story_counts_by_trope_id.get(selected_trope.id, 0),
                    }
                ],
                story_counts_by_trope_id=story_counts_by_trope_id,
                color=similarity_to_color(1.0, min_similarity),
                has_location=has_location,
            )
        )

    related_by_story_id: dict[str, dict] = {}
    missing_related_coords = 0
    for related_trope in related_tropes:
        for entry in stories_by_trope_id.get(related_trope["id"], []):
            if entry.story_id in selected_story_ids:
                continue

            marker = related_by_story_id.get(entry.story_id)
            if marker is None:
                coordinates = parse_space_coord(entry.fields.get("space coord", ""))
                has_location = coordinates is not None
                if not has_location:
                    missing_related_coords += 1
                marker = _marker_payload(
                    entry=entry,
                    coordinates=coordinates,
                    kind="related",
                    similarity=float(related_trope["score"]),
                    matched_tropes=[],
                    story_counts_by_trope_id=story_counts_by_trope_id,
                    color=similarity_to_color(float(related_trope["score"]), min_similarity),
                    has_location=has_location,
                )
                related_by_story_id[entry.story_id] = marker

            marker["matched_tropes"].append(
                {
                    "id": related_trope["id"],
                    "text": related_trope["text"],
                    "score": float(related_trope["score"]),
                    "story_count": int(related_trope["story_count"]),
                }
            )
            if float(related_trope["score"]) > marker["similarity"]:
                marker["similarity"] = float(related_trope["score"])
                marker["color"] = similarity_to_color(float(related_trope["score"]), min_similarity)

    related_markers = sorted(
        related_by_story_id.values(),
        key=lambda item: (-item["similarity"], item["title"].lower(), item["story_id"]),
    )
    for marker in related_markers:
        marker["matched_tropes"].sort(key=lambda item: (-item["score"], item["text"].lower()))

    original_markers = sorted(
        original_markers,
        key=lambda item: (item["title"].lower(), item["story_id"]),
    )

    locatable_originals = [marker for marker in original_markers if marker["has_location"] and marker["coordinates"] is not None]
    connections = []
    if locatable_originals:
        for marker in related_markers:
            if not marker["has_location"] or marker["coordinates"] is None:
                continue
            nearest = min(
                locatable_originals,
                key=lambda original: _great_circle_distance(
                    tuple(original["coordinates"]),
                    tuple(marker["coordinates"]),
                ),
            )
            connections.append(
                {
                    "source_story_id": nearest["story_id"],
                    "target_story_id": marker["story_id"],
                    "source_coordinates": nearest["coordinates"],
                    "target_coordinates": marker["coordinates"],
                    "similarity": marker["similarity"],
                    "color": marker["color"],
                }
            )

    visible_markers = [
        marker
        for marker in (original_markers + related_markers)
        if marker["has_location"] and marker["coordinates"] is not None
    ]
    bounds = None
    if visible_markers:
        latitudes = [marker["coordinates"][0] for marker in visible_markers]
        longitudes = [marker["coordinates"][1] for marker in visible_markers]
        bounds = [[min(latitudes), min(longitudes)], [max(latitudes), max(longitudes)]]

    return {
        "selected_trope": _selected_trope_payload(selected_trope, active_dataset.id, session),
        "selected_trope_candidates": [],
        "related_tropes": related_tropes,
        "original_markers": original_markers,
        "related_markers": related_markers,
        "connections": connections,
        "bounds": bounds,
        "missing_original_coords": missing_original_coords,
        "missing_related_coords": missing_related_coords,
    }


def _get_active_dataset(session: Session) -> Dataset | None:
    return session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))


def _get_active_trope(session: Session, dataset_id: str, trope_id: str) -> Trope | None:
    return session.scalar(
        select(Trope)
        .join(StoryTrope, StoryTrope.trope_id == Trope.id)
        .join(Story, Story.id == StoryTrope.story_id)
        .where(
            Trope.id == trope_id,
            Story.dataset_id == dataset_id,
        )
        .group_by(Trope.id)
    )


def _selected_trope_payload(trope: Trope, dataset_id: str, session: Session) -> dict:
    story_count = (
        session.scalar(
            select(func.count(func.distinct(Story.id)))
            .select_from(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(
                Story.dataset_id == dataset_id,
                StoryTrope.trope_id == trope.id,
            )
        )
        or 0
    )
    return {
        "id": trope.id,
        "text": trope.text,
        "story_count": int(story_count),
    }


def _serialize_candidate(item: dict) -> dict:
    return {
        "id": item["id"],
        "text": item["text"],
        "story_count": int(item["story_count"]),
        "score": float(item["score"]),
    }


def _load_active_story_entries(session: Session, dataset_id: str) -> list[StoryEntry]:
    stories = session.scalars(
        select(Story)
        .where(Story.dataset_id == dataset_id)
        .options(selectinload(Story.trope_links).selectinload(StoryTrope.trope))
        .order_by(Story.source_row_number, Story.created_at, Story.id)
    ).all()
    return [
        StoryEntry(
            story_id=story.id,
            source_row_number=story.source_row_number,
            fields={key: clean_text(value) for key, value in (story.fields_json or {}).items()},
            tropes=[
                {
                    "id": link.trope.id,
                    "text": link.trope.text,
                }
                for link in sorted(
                    story.trope_links,
                    key=lambda item: (
                        item.position is None,
                        item.position if item.position is not None else 0,
                        item.created_at,
                        item.trope.text if item.trope is not None else "",
                    ),
                )
                if link.trope is not None
            ],
        )
        for story in stories
    ]


def _great_circle_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    arc = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(min(1.0, math.sqrt(arc)))


def _marker_payload(
    *,
    entry: StoryEntry,
    coordinates: tuple[float, float] | None,
    kind: str,
    similarity: float,
    matched_tropes: list[dict],
    story_counts_by_trope_id: dict[str, int],
    color: str,
    has_location: bool,
) -> dict:
    return {
        "story_id": entry.story_id,
        "source_row_number": entry.source_row_number,
        "coordinates": list(coordinates) if coordinates is not None else None,
        "kind": kind,
        "similarity": similarity,
        "matched_tropes": matched_tropes,
        "story_tropes": [
            {
                "id": trope["id"],
                "text": trope["text"],
                "story_count": story_counts_by_trope_id.get(trope["id"], 0),
            }
            for trope in entry.tropes
        ],
        "color": color,
        "title": entry_title(entry),
        "hover_title": english_story_title(entry),
        "abstract": primary_abstract(entry),
        "has_location": has_location,
    }
