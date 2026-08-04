from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.coordinates import parse_space_coord
from app.core.csv_schema import THEME_FIELD
from app.core.parsing import clean_text, split_themes
from app.db.models import (
    Dataset,
    DatasetStatus,
    Story,
    StoryKeyword,
    StoryTheme,
    StoryTrope,
    TermKind,
    Trope,
)


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
FILTER_COMPLETENESS_FIELD = "completeness"
FILTER_WHITESPACE_RE = re.compile(r"\s+")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class StoryEntry:
    story_id: str
    source_row_number: int | None
    completeness: str = "complete"
    fields: dict[str, str] = field(default_factory=dict)
    tropes: list[dict] = field(default_factory=list)
    themes: list[dict] = field(default_factory=list)
    keywords: list[dict] = field(default_factory=list)


@dataclass
class StoryFieldFilter:
    field: str
    selected_values: list[str]


@dataclass
class SelectedTermFilter:
    id: str
    text: str


@dataclass
class StoryFilterSet:
    id: str
    label: str
    color: str
    filters: list[StoryFieldFilter]
    selected_themes: list[SelectedTermFilter]
    selected_tropes: list[SelectedTermFilter]
    selected_keywords: list[SelectedTermFilter]


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
    story_filters: list[dict] | None = None,
    story_filter_sets: list[dict] | None = None,
    min_similarity: float = 0.62,
    related_limit: int = 60,
    candidate_limit: int = 12,
) -> dict:
    normalized_story_filters = _normalize_story_filters(story_filters)
    normalized_story_filter_sets = _normalize_story_filter_sets(story_filter_sets)
    if selected_trope_id is None and not normalized_story_filters and not normalized_story_filter_sets:
        if not clean_text(query):
            raise ExplorationValidationError(
                "Provide selected_trope_id, a query, story_filters, or story_filter_sets to build exploration."
            )
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
            "filter_set_results": [],
        }

    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise ExplorationNotFoundError("No active dataset is available for exploration.")

    stories = _load_active_story_entries(session, active_dataset.id)
    active_story_counts_by_trope_id = _story_counts_by_trope_id(stories)
    active_story_counts_by_theme_id = _story_counts_by_term_id(stories, "themes")
    active_story_counts_by_keyword_id = _story_counts_by_term_id(stories, "keywords")

    if normalized_story_filter_sets:
        selected_trope = None
        if selected_trope_id is not None:
            selected_trope = _get_active_trope(session, active_dataset.id, selected_trope_id)
            if selected_trope is None:
                raise ExplorationNotFoundError("Selected trope not found in the active dataset.")

        filter_set_results = []
        for filter_set in normalized_story_filter_sets:
            filtered_entries = _apply_story_filters(
                stories,
                filter_set.filters,
                selected_themes=filter_set.selected_themes,
                selected_tropes=filter_set.selected_tropes,
                selected_keywords=filter_set.selected_keywords,
            )
            if selected_trope is None:
                set_result = _filtered_story_map_response(
                    filtered_entries,
                    min_similarity=min_similarity,
                    original_color=filter_set.color,
                    selected_tropes=filter_set.selected_tropes,
                    filter_set_id=filter_set.id,
                    filter_set_label=filter_set.label,
                )
            else:
                set_result = _selected_trope_story_map_response(
                    session,
                    search_service,
                    active_dataset.id,
                    selected_trope,
                    filtered_entries,
                    min_similarity=min_similarity,
                    related_limit=related_limit,
                    original_color=filter_set.color,
                    related_color=_lighten_hex_color(filter_set.color, amount=0.45),
                    connection_color=filter_set.color,
                    filter_set_id=filter_set.id,
                    filter_set_label=filter_set.label,
                )

            filter_set_results.append(
                {
                    "filter_set_id": filter_set.id,
                    "filter_set_label": filter_set.label,
                    "filter_set_color": filter_set.color,
                    "filters": [
                        {
                            "field": story_filter.field,
                            "selected_values": list(story_filter.selected_values),
                        }
                        for story_filter in filter_set.filters
                    ],
                    "selected_themes": _serialize_selected_term_filters(
                        filter_set.selected_themes,
                        active_story_counts_by_theme_id,
                    ),
                    "selected_tropes": _serialize_selected_term_filters(
                        filter_set.selected_tropes,
                        active_story_counts_by_trope_id,
                    ),
                    "selected_keywords": _serialize_selected_term_filters(
                        filter_set.selected_keywords,
                        active_story_counts_by_keyword_id,
                    ),
                    "related_tropes": set_result["related_tropes"],
                    "original_markers": set_result["original_markers"],
                    "related_markers": set_result["related_markers"],
                    "connections": set_result["connections"],
                    "bounds": set_result["bounds"],
                    "missing_original_coords": set_result["missing_original_coords"],
                    "missing_related_coords": set_result["missing_related_coords"],
                }
            )

        return _aggregate_filter_set_results(
            selected_trope=_selected_trope_payload(selected_trope, active_dataset.id, session) if selected_trope else None,
            filter_set_results=filter_set_results,
        )

    filtered_stories = _apply_story_filters(stories, normalized_story_filters)

    if selected_trope_id is None:
        return _filtered_story_map_response(
            filtered_stories,
            min_similarity=min_similarity,
        )

    selected_trope = _get_active_trope(session, active_dataset.id, selected_trope_id)
    if selected_trope is None:
        raise ExplorationNotFoundError("Selected trope not found in the active dataset.")

    return _selected_trope_story_map_response(
        session,
        search_service,
        active_dataset.id,
        selected_trope,
        filtered_stories,
        min_similarity=min_similarity,
        related_limit=related_limit,
    )


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
        .options(selectinload(Story.theme_links).selectinload(StoryTheme.theme))
        .options(selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword))
        .order_by(Story.source_row_number, Story.created_at, Story.id)
    ).all()
    return [
        StoryEntry(
            story_id=story.id,
            source_row_number=story.source_row_number,
            completeness=story.completeness.value,
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
            themes=[
                {
                    "id": link.theme.id,
                    "text": link.theme.text,
                }
                for link in sorted(
                    story.theme_links,
                    key=lambda item: (
                        item.position is None,
                        item.position if item.position is not None else 0,
                        item.created_at,
                        item.theme.text if item.theme is not None else "",
                    ),
                )
                if link.theme is not None
            ],
            keywords=[
                {
                    "id": link.keyword.id,
                    "text": link.keyword.text,
                }
                for link in sorted(
                    story.keyword_links,
                    key=lambda item: (
                        item.position is None,
                        item.position if item.position is not None else 0,
                        item.created_at,
                        item.keyword.text if item.keyword is not None else "",
                    ),
                )
                if link.keyword is not None
            ],
        )
        for story in stories
    ]


def _normalize_filter_value(value: str) -> str:
    return FILTER_WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", clean_text(value)).replace("\ufeff", " ")).strip()


def _normalize_filter_set_color(value: str) -> str:
    normalized = clean_text(value)
    if HEX_COLOR_RE.match(normalized):
        return normalized.lower()
    return "#1f7177"


def _normalize_story_filters(story_filters: list[dict] | None) -> list[StoryFieldFilter]:
    normalized_filters: list[StoryFieldFilter] = []
    for item in story_filters or []:
        field = clean_text((item or {}).get("field", ""))
        raw_selected_values = [
            clean_text(value)
            for value in (item or {}).get("selected_values", [])
            if clean_text(value)
        ]
        normalized_selected_values = [
            normalized
            for normalized in (
                _normalize_filter_value(value)
                for value in raw_selected_values
            )
            if normalized
        ]
        selected_values = (
            [
                _normalize_filter_value(theme)
                for value in raw_selected_values
                for theme in split_themes(value)
                if _normalize_filter_value(theme)
            ]
            if field == THEME_FIELD
            else normalized_selected_values
        )
        if not field or not selected_values:
            continue
        normalized_filters.append(
            StoryFieldFilter(
                field=field,
                selected_values=list(dict.fromkeys(selected_values)),
            )
        )
    return normalized_filters


def _normalize_selected_term_filters(selected_terms: list[dict] | None) -> list[SelectedTermFilter]:
    normalized_selected_terms: list[SelectedTermFilter] = []
    seen_ids: set[str] = set()
    for item in selected_terms or []:
        term_id = clean_text((item or {}).get("id", ""))
        if not term_id or term_id in seen_ids:
            continue
        seen_ids.add(term_id)
        normalized_selected_terms.append(
            SelectedTermFilter(
                id=term_id,
                text=clean_text((item or {}).get("text", "")) or term_id,
            )
        )
    return normalized_selected_terms


def _normalize_story_filter_sets(story_filter_sets: list[dict] | None) -> list[StoryFilterSet]:
    normalized_sets: list[StoryFilterSet] = []
    for index, item in enumerate(story_filter_sets or []):
        filters = _normalize_story_filters((item or {}).get("filters", []))
        selected_themes = _normalize_selected_term_filters((item or {}).get("selected_themes", []))
        selected_tropes = _normalize_selected_term_filters((item or {}).get("selected_tropes", []))
        selected_keywords = _normalize_selected_term_filters((item or {}).get("selected_keywords", []))
        if not filters and not selected_themes and not selected_tropes and not selected_keywords:
            continue
        normalized_sets.append(
            StoryFilterSet(
                id=clean_text((item or {}).get("id", "")) or f"set-{index + 1}",
                label=clean_text((item or {}).get("label", "")) or f"Set {index + 1}",
                color=_normalize_filter_set_color((item or {}).get("color", "")),
                filters=filters,
                selected_themes=selected_themes,
                selected_tropes=selected_tropes,
                selected_keywords=selected_keywords,
            )
        )
    return normalized_sets


def _story_filter_value(entry: StoryEntry, field: str) -> str:
    if field == FILTER_COMPLETENESS_FIELD:
        return entry.completeness
    return entry.fields.get(field, "")


def _entry_matches_story_filters(
    entry: StoryEntry,
    story_filters: list[StoryFieldFilter],
    *,
    selected_themes: list[SelectedTermFilter] | None = None,
    selected_tropes: list[SelectedTermFilter] | None = None,
    selected_keywords: list[SelectedTermFilter] | None = None,
) -> bool:
    matches_fields = all(
        (
            any(
                _normalize_filter_value(theme) in story_filter.selected_values
                for theme in split_themes(_story_filter_value(entry, story_filter.field))
            )
            if story_filter.field == THEME_FIELD
            else _normalize_filter_value(_story_filter_value(entry, story_filter.field)) in story_filter.selected_values
        )
        for story_filter in story_filters
    )
    if not matches_fields:
        return False
    selected_term_groups = (
        (entry.themes, selected_themes),
        (entry.tropes, selected_tropes),
        (entry.keywords, selected_keywords),
    )
    active_selected_term_groups = [
        (entry_terms, selected_terms)
        for entry_terms, selected_terms in selected_term_groups
        if selected_terms
    ]
    if not active_selected_term_groups:
        return True
    return any(
        _entry_matches_selected_terms(entry_terms, selected_terms)
        for entry_terms, selected_terms in active_selected_term_groups
    )


def _entry_matches_selected_terms(
    entry_terms: list[dict], selected_terms: list[SelectedTermFilter] | None
) -> bool:
    if not selected_terms:
        return False
    selected_term_ids = {term.id for term in selected_terms}
    return any(term["id"] in selected_term_ids for term in entry_terms)


def _apply_story_filters(
    entries: list[StoryEntry],
    story_filters: list[StoryFieldFilter],
    *,
    selected_themes: list[SelectedTermFilter] | None = None,
    selected_tropes: list[SelectedTermFilter] | None = None,
    selected_keywords: list[SelectedTermFilter] | None = None,
) -> list[StoryEntry]:
    if not story_filters and not selected_themes and not selected_tropes and not selected_keywords:
        return entries
    return [
        entry
        for entry in entries
        if _entry_matches_story_filters(
            entry,
            story_filters,
            selected_themes=selected_themes,
            selected_tropes=selected_tropes,
            selected_keywords=selected_keywords,
        )
    ]


def _story_counts_by_trope_id(entries: list[StoryEntry]) -> dict[str, int]:
    return _story_counts_by_term_id(entries, "tropes")


def _story_counts_by_term_id(entries: list[StoryEntry], attribute: str) -> dict[str, int]:
    story_counts_by_term_id: dict[str, int] = {}
    for entry in entries:
        for term in getattr(entry, attribute):
            story_counts_by_term_id[term["id"]] = story_counts_by_term_id.get(term["id"], 0) + 1
    return story_counts_by_term_id


def _serialize_selected_term_filters(
    selected_terms: list[SelectedTermFilter],
    story_counts_by_term_id: dict[str, int],
) -> list[dict]:
    return [
        {
            "id": term.id,
            "text": term.text,
            "story_count": int(story_counts_by_term_id.get(term.id, 0)),
        }
        for term in selected_terms
    ]


def _filtered_story_map_response(
    entries: list[StoryEntry],
    *,
    min_similarity: float,
    original_color: str | None = None,
    selected_tropes: list[SelectedTermFilter] | None = None,
    filter_set_id: str | None = None,
    filter_set_label: str | None = None,
) -> dict:
    original_markers: list[dict] = []
    missing_original_coords = 0
    story_counts_by_trope_id = _story_counts_by_trope_id(entries)
    selected_trope_ids = {trope.id for trope in selected_tropes or []}

    for entry in entries:
        coordinates = parse_space_coord(entry.fields.get("space coord", ""))
        has_location = coordinates is not None
        if not has_location:
            missing_original_coords += 1
        matched_tropes = [
            {
                "id": trope["id"],
                "text": trope["text"],
                "score": 1.0,
                "story_count": story_counts_by_trope_id.get(trope["id"], 0),
            }
            for trope in entry.tropes
            if trope["id"] in selected_trope_ids
        ]
        original_markers.append(
            _marker_payload(
                entry=entry,
                coordinates=coordinates,
                kind="original",
                similarity=1.0,
                matched_tropes=matched_tropes,
                story_counts_by_trope_id=story_counts_by_trope_id,
                color=original_color or similarity_to_color(1.0, min_similarity),
                has_location=has_location,
                filter_set_id=filter_set_id,
                filter_set_label=filter_set_label,
            )
        )

    original_markers = sorted(
        original_markers,
        key=lambda item: (item["title"].lower(), item["story_id"]),
    )

    visible_markers = [
        marker
        for marker in original_markers
        if marker["has_location"] and marker["coordinates"] is not None
    ]
    bounds = None
    if visible_markers:
        latitudes = [marker["coordinates"][0] for marker in visible_markers]
        longitudes = [marker["coordinates"][1] for marker in visible_markers]
        bounds = [[min(latitudes), min(longitudes)], [max(latitudes), max(longitudes)]]

    return {
        "selected_trope": None,
        "selected_trope_candidates": [],
        "related_tropes": [],
        "original_markers": original_markers,
        "related_markers": [],
        "connections": [],
        "bounds": bounds,
        "missing_original_coords": missing_original_coords,
        "missing_related_coords": 0,
        "filter_set_results": [],
    }


def _selected_trope_story_map_response(
    session: Session,
    search_service,
    dataset_id: str,
    selected_trope: Trope,
    entries: list[StoryEntry],
    *,
    min_similarity: float,
    related_limit: int,
    original_color: str | None = None,
    related_color: str | None = None,
    connection_color: str | None = None,
    filter_set_id: str | None = None,
    filter_set_label: str | None = None,
) -> dict:
    stories_by_trope_id: dict[str, list[StoryEntry]] = {}
    for entry in entries:
        for trope in entry.tropes:
            stories_by_trope_id.setdefault(trope["id"], []).append(entry)
    story_counts_by_trope_id = {
        trope_id: len(trope_entries)
        for trope_id, trope_entries in stories_by_trope_id.items()
    }

    selected_entries = stories_by_trope_id.get(selected_trope.id, [])
    if not selected_entries:
        return {
            "selected_trope": _selected_trope_payload(selected_trope, dataset_id, session),
            "selected_trope_candidates": [],
            "related_tropes": [],
            "original_markers": [],
            "related_markers": [],
            "connections": [],
            "bounds": None,
            "missing_original_coords": 0,
            "missing_related_coords": 0,
            "filter_set_results": [],
        }

    selected_story_ids = {entry.story_id for entry in selected_entries}
    related_search = search_service.search_terms(session, TermKind.TROPE, selected_trope.text, limit=related_limit + 1)
    related_tropes = []
    for item in related_search["items"]:
        if item["id"] == selected_trope.id:
            continue
        if float(item["score"]) < min_similarity:
            continue
        candidate_story_ids = {
            entry.story_id
            for entry in stories_by_trope_id.get(item["id"], [])
            if entry.story_id not in selected_story_ids
        }
        if not candidate_story_ids:
            continue
        related_tropes.append(_serialize_candidate(item))
        if len(related_tropes) >= related_limit:
            break

    original_markers: list[dict] = []
    missing_original_coords = 0
    for entry in selected_entries:
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
                color=original_color or similarity_to_color(1.0, min_similarity),
                has_location=has_location,
                filter_set_id=filter_set_id,
                filter_set_label=filter_set_label,
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
                    color=related_color or similarity_to_color(float(related_trope["score"]), min_similarity),
                    has_location=has_location,
                    filter_set_id=filter_set_id,
                    filter_set_label=filter_set_label,
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
                if related_color is None:
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
                    "color": connection_color or marker["color"],
                    "filter_set_id": filter_set_id,
                    "filter_set_label": filter_set_label,
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
        "selected_trope": _selected_trope_payload(selected_trope, dataset_id, session),
        "selected_trope_candidates": [],
        "related_tropes": related_tropes,
        "original_markers": original_markers,
        "related_markers": related_markers,
        "connections": connections,
        "bounds": bounds,
        "missing_original_coords": missing_original_coords,
        "missing_related_coords": missing_related_coords,
        "filter_set_results": [],
    }


def _aggregate_filter_set_results(
    *,
    selected_trope: dict | None,
    filter_set_results: list[dict],
) -> dict:
    return {
        "selected_trope": selected_trope,
        "selected_trope_candidates": [],
        "related_tropes": _combine_related_tropes(filter_set_results),
        "original_markers": [],
        "related_markers": [],
        "connections": [],
        "bounds": _merge_bounds([item["bounds"] for item in filter_set_results]),
        "missing_original_coords": sum(item["missing_original_coords"] for item in filter_set_results),
        "missing_related_coords": sum(item["missing_related_coords"] for item in filter_set_results),
        "filter_set_results": filter_set_results,
    }


def _combine_related_tropes(filter_set_results: list[dict]) -> list[dict]:
    combined: dict[str, dict] = {}
    for result in filter_set_results:
        for trope in result.get("related_tropes", []):
            existing = combined.get(trope["id"])
            if existing is None or float(trope["score"]) > float(existing["score"]):
                combined[trope["id"]] = {
                    "id": trope["id"],
                    "text": trope["text"],
                    "story_count": int(trope["story_count"]),
                    "score": float(trope["score"]),
                }
    return sorted(combined.values(), key=lambda item: (-item["score"], item["text"].lower(), item["id"]))


def _merge_bounds(bounds_list: list[list[list[float]] | None]) -> list[list[float]] | None:
    points: list[tuple[float, float]] = []
    for bounds in bounds_list:
        if not bounds or len(bounds) != 2:
            continue
        try:
            south_west = (float(bounds[0][0]), float(bounds[0][1]))
            north_east = (float(bounds[1][0]), float(bounds[1][1]))
        except (TypeError, ValueError, IndexError):
            continue
        points.extend([south_west, north_east])
    if not points:
        return None
    latitudes = [point[0] for point in points]
    longitudes = [point[1] for point in points]
    return [[min(latitudes), min(longitudes)], [max(latitudes), max(longitudes)]]


def _lighten_hex_color(color: str, *, amount: float = 0.35) -> str:
    if not HEX_COLOR_RE.match(color):
        return color
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    adjusted = (
        round(red + (255 - red) * amount),
        round(green + (255 - green) * amount),
        round(blue + (255 - blue) * amount),
    )
    return f"#{adjusted[0]:02x}{adjusted[1]:02x}{adjusted[2]:02x}"


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
    filter_set_id: str | None = None,
    filter_set_label: str | None = None,
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
        "filter_set_id": filter_set_id,
        "filter_set_label": filter_set_label,
    }
