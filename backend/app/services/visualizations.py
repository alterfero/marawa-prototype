from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.coordinates import parse_space_coord
from app.core.parsing import clean_text
from app.core.projection import project_lon_lat_equirectangular
from app.db.models import Dataset, DatasetStatus, Story, StoryTrope, TermKind, Trope


TITLE_FIELDS = [
    "Story title (Eng)",
    "Story title (French)",
    "Story title (other)",
]


class VisualizationError(ValueError):
    """Base error for experimental visualization requests."""


class VisualizationNotFoundError(VisualizationError):
    """Raised when the requested visualization basis cannot be resolved."""


class VisualizationValidationError(VisualizationError):
    """Raised when the visualization request is invalid."""


@dataclass
class SelectedTropeRecord:
    id: str
    text: str
    score: float


@dataclass
class StorySelection:
    story: Story
    title: str
    lat: float | None
    lon: float | None
    match_score: float
    contains_selected_trope: bool

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None


def build_trope_sequence_graph(
    session: Session,
    search_service,
    *,
    selected_trope_id: str | None,
    query: str | None,
    similarity_threshold: float = 0.65,
    max_stories: int = 150,
    max_links_per_node: int = 4,
    vertical_spacing: float = 30,
) -> dict:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise VisualizationNotFoundError("No active dataset is available for visualization.")

    cleaned_query = clean_text(query)
    if not cleaned_query and not clean_text(selected_trope_id):
        raise VisualizationValidationError("Provide selected_trope_id or a query to build the visualization.")

    selected_trope = _resolve_selected_trope(
        session,
        search_service,
        dataset_id=active_dataset.id,
        selected_trope_id=selected_trope_id,
        query=cleaned_query or None,
    )

    trope_count = (
        session.scalar(
            select(func.count(func.distinct(StoryTrope.trope_id)))
            .select_from(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(Story.dataset_id == active_dataset.id)
        )
        or 0
    )
    similar_tropes = search_service.search_terms(
        session,
        TermKind.TROPE,
        selected_trope.text,
        limit=max(int(trope_count), 1),
    )
    selected_similarity_by_trope_id = {
        item["id"]: float(item["score"])
        for item in similar_tropes["items"]
        if float(item["score"]) >= similarity_threshold
    }
    selected_similarity_by_trope_id[selected_trope.id] = 1.0

    stories = _load_active_stories(session, active_dataset.id)
    eligible_stories: list[StorySelection] = []
    for story in stories:
        ordered_links = _ordered_trope_links(story)
        matching_scores = [selected_similarity_by_trope_id[link.trope_id] for link in ordered_links if link.trope_id in selected_similarity_by_trope_id]
        if not matching_scores:
            continue
        coordinates = parse_space_coord((story.fields_json or {}).get("space coord", ""))
        eligible_stories.append(
            StorySelection(
                story=story,
                title=_story_title(story),
                lat=coordinates[0] if coordinates is not None else None,
                lon=coordinates[1] if coordinates is not None else None,
                match_score=max(matching_scores),
                contains_selected_trope=any(link.trope_id == selected_trope.id for link in ordered_links),
            )
        )

    eligible_stories.sort(
        key=lambda item: (
            0 if item.contains_selected_trope else 1,
            -item.match_score,
            item.story.source_row_number is None,
            item.story.source_row_number if item.story.source_row_number is not None else 0,
            item.title.lower(),
            item.story.id,
        )
    )

    warnings: list[str] = []
    if len(eligible_stories) > max_stories:
        warnings.append(
            f"Capped the graph to {max_stories} stories from {len(eligible_stories)} eligible matches."
        )
    selected_stories = eligible_stories[:max_stories]

    excluded_stories = [story for story in selected_stories if not story.has_location]
    included_stories = [story for story in selected_stories if story.has_location]
    if excluded_stories:
        warnings.append(
            f"Excluded {len(excluded_stories)} selected stories without valid coordinates from the 3D graph."
        )

    centered_projection_by_story_id: dict[str, tuple[float, float]] = {}
    if included_stories:
        absolute_projections = {
            story_selection.story.id: project_lon_lat_equirectangular(story_selection.lon, story_selection.lat)
            for story_selection in included_stories
            if story_selection.lon is not None and story_selection.lat is not None
        }
        if absolute_projections:
            center_x = sum(point.x for point in absolute_projections.values()) / len(absolute_projections)
            center_y = sum(point.y for point in absolute_projections.values()) / len(absolute_projections)
            centered_projection_by_story_id = {
                story_id: (
                    round(point.x - center_x, 6),
                    round(point.y - center_y, 6),
                )
                for story_id, point in absolute_projections.items()
            }

    nodes: list[dict] = []
    links: list[dict] = []
    occurrence_nodes: list[dict] = []

    for story_selection in included_stories:
        assert story_selection.lat is not None
        assert story_selection.lon is not None

        projection_x, projection_y = centered_projection_by_story_id.get(story_selection.story.id, (0.0, 0.0))
        anchor_id = f"story:{story_selection.story.id}:anchor"
        ordered_links = _ordered_trope_links(story_selection.story)

        nodes.append(
            {
                "id": anchor_id,
                "kind": "story_anchor",
                "story_id": story_selection.story.id,
                "story_title": story_selection.title,
                "source_row_number": story_selection.story.source_row_number,
                "story_match_score": round(story_selection.match_score, 6),
                "lat": story_selection.lat,
                "lon": story_selection.lon,
                "x": projection_x,
                "y": projection_y,
                "z": 0.0,
                "fx": projection_x,
                "fy": projection_y,
                "fz": 0.0,
                "has_location": True,
                "occurrence_count": len(ordered_links),
            }
        )

        previous_occurrence_id: str | None = None
        for sequence_index, link in enumerate(ordered_links):
            node_id = f"story:{story_selection.story.id}:trope:{link.trope_id}"
            target_z = round(float(vertical_spacing) * float(sequence_index + 1), 6)
            occurrence_node = {
                "id": node_id,
                "kind": "trope_occurrence",
                "story_id": story_selection.story.id,
                "story_title": story_selection.title,
                "source_row_number": story_selection.story.source_row_number,
                "trope_id": link.trope.id,
                "trope_text": link.trope.text,
                "sequence_index": sequence_index,
                "lat": story_selection.lat,
                "lon": story_selection.lon,
                "anchor_x": projection_x,
                "anchor_y": projection_y,
                "target_z": target_z,
                "x": projection_x,
                "y": projection_y,
                "z": target_z,
                "has_location": True,
                "status": link.status.value,
                "origin": link.origin.value,
                "is_selected_trope": link.trope_id == selected_trope.id,
                "selected_similarity_score": round(float(selected_similarity_by_trope_id.get(link.trope_id, 0.0)), 6),
            }
            nodes.append(occurrence_node)
            occurrence_nodes.append(occurrence_node)
            links.append(
                {
                    "source": anchor_id,
                    "target": node_id,
                    "kind": "anchor",
                    "strength": 0.9,
                }
            )
            if previous_occurrence_id is not None:
                links.append(
                    {
                        "source": previous_occurrence_id,
                        "target": node_id,
                        "kind": "sequence",
                        "strength": 0.8,
                    }
                )
            previous_occurrence_id = node_id

    semantic_candidates: list[tuple[float, str, str]] = []
    trope_ids = sorted({node["trope_id"] for node in occurrence_nodes})
    pairwise_similarity = search_service.get_trope_pairwise_similarities(
        session,
        trope_ids,
        minimum_score=similarity_threshold,
    )

    for index, left_node in enumerate(occurrence_nodes):
        for right_node in occurrence_nodes[index + 1 :]:
            if left_node["story_id"] == right_node["story_id"]:
                continue
            if left_node["trope_id"] == right_node["trope_id"]:
                similarity = 1.0
            else:
                similarity = pairwise_similarity.get(_pair_key(left_node["trope_id"], right_node["trope_id"]))
            if similarity is None or similarity < similarity_threshold:
                continue
            semantic_candidates.append((float(similarity), left_node["id"], right_node["id"]))

    semantic_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    semantic_degree_by_node_id = {node["id"]: 0 for node in occurrence_nodes}
    for similarity, source_id, target_id in semantic_candidates:
        if max_links_per_node <= 0:
            break
        if semantic_degree_by_node_id[source_id] >= max_links_per_node:
            continue
        if semantic_degree_by_node_id[target_id] >= max_links_per_node:
            continue
        semantic_degree_by_node_id[source_id] += 1
        semantic_degree_by_node_id[target_id] += 1
        links.append(
            {
                "source": source_id,
                "target": target_id,
                "kind": "semantic",
                "similarity": round(float(similarity), 6),
                "strength": round(float(similarity) * 0.15, 6),
            }
        )

    return {
        "layout_basis": {
            "selected_trope": {
                "id": selected_trope.id,
                "text": selected_trope.text,
                "score": 1.0,
            },
            "query": cleaned_query or None,
            "similarity_threshold": similarity_threshold,
            "max_stories": max_stories,
            "max_links_per_node": max_links_per_node,
            "sequence_axis_label": "assignment order",
        },
        "nodes": nodes,
        "links": links,
        "warnings": warnings,
    }


def _get_active_dataset(session: Session) -> Dataset | None:
    return session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))


def _resolve_selected_trope(
    session: Session,
    search_service,
    *,
    dataset_id: str,
    selected_trope_id: str | None,
    query: str | None,
) -> SelectedTropeRecord:
    if clean_text(selected_trope_id):
        trope = session.scalar(
            select(Trope)
            .join(StoryTrope, StoryTrope.trope_id == Trope.id)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(
                Trope.id == clean_text(selected_trope_id),
                Story.dataset_id == dataset_id,
            )
            .group_by(Trope.id)
        )
        if trope is None:
            raise VisualizationNotFoundError("Selected trope not found in the active dataset.")
        return SelectedTropeRecord(id=trope.id, text=trope.text, score=1.0)

    if not query:
        raise VisualizationValidationError("Provide selected_trope_id or a query to build the visualization.")

    candidates = search_service.search_terms(session, TermKind.TROPE, query, limit=12)
    if not candidates["items"]:
        raise VisualizationNotFoundError("No active trope matched the requested query.")
    top_candidate = candidates["items"][0]
    return SelectedTropeRecord(
        id=top_candidate["id"],
        text=top_candidate["text"],
        score=float(top_candidate["score"]),
    )


def _load_active_stories(session: Session, dataset_id: str) -> list[Story]:
    return session.scalars(
        select(Story)
        .where(Story.dataset_id == dataset_id)
        .options(selectinload(Story.trope_links).selectinload(StoryTrope.trope))
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()


def _ordered_trope_links(story: Story) -> list[StoryTrope]:
    return sorted(
        [link for link in story.trope_links if link.trope is not None],
        key=lambda item: (
            item.position is None,
            item.position if item.position is not None else 0,
            item.created_at,
            item.trope.text if item.trope is not None else "",
        ),
    )


def _pair_key(left_trope_id: str, right_trope_id: str) -> tuple[str, str]:
    return (left_trope_id, right_trope_id) if left_trope_id < right_trope_id else (right_trope_id, left_trope_id)


def _story_title(story: Story) -> str:
    fields = story.fields_json or {}
    for field_name in TITLE_FIELDS:
        title = clean_text(fields.get(field_name, ""))
        if title:
            return title
    return story.id
