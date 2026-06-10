from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.core.parsing import clean_text, dedupe_preserve_order, normalize_text, serialize_keywords, serialize_tropes
from app.db.models import (
    AssignmentStatus,
    Dataset,
    DatasetStatus,
    Keyword,
    Story,
    StoryKeyword,
    StoryTrope,
    StoryTropeOrigin,
    Trope,
)
from app.services.jobs import queue_job


class StoryServiceError(ValueError):
    """Base error for story review operations."""


class StoryNotFoundError(StoryServiceError):
    """Raised when a story is not in the active dataset."""


class StoryTropeNotFoundError(StoryServiceError):
    """Raised when a story-trope assignment cannot be found."""


class TropeNotFoundError(StoryServiceError):
    """Raised when a canonical trope cannot be found."""


class StoryVersionConflictError(StoryServiceError):
    def __init__(self, current_story_version: int) -> None:
        super().__init__("Story version does not match the current server version.")
        self.current_story_version = current_story_version


class StoryMutationValidationError(StoryServiceError):
    """Raised when a story mutation request is invalid."""


class ActiveDatasetNotFoundError(StoryServiceError):
    """Raised when a mutation requires an active dataset but none exists."""


class DatasetVersionConflictError(StoryServiceError):
    def __init__(self, current_dataset_version: int) -> None:
        super().__init__("Active dataset version does not match the current server version.")
        self.current_dataset_version = current_dataset_version


def list_active_stories(session: Session) -> dict:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return {
            "items": [],
            "total": 0,
        }

    stories = session.scalars(
        select(Story)
        .where(Story.dataset_id == active_dataset.id)
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
        )
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()

    return {
        "items": [_serialize_story_summary(story) for story in stories],
        "total": len(stories),
    }


def get_story_detail(session: Session, story_id: str) -> dict:
    _, story = _get_active_story(session, story_id)
    return _serialize_story_detail(story)


def get_story_tropes(session: Session, story_id: str) -> dict:
    _, story = _get_active_story(session, story_id)
    return {
        "story_id": story.id,
        "story_version": story.version,
        "items": [_serialize_story_trope(link) for link in _ordered_trope_links(story)],
    }


def create_story(
    session: Session,
    *,
    expected_dataset_version: int,
    fields: Mapping[str, object] | None = None,
    tropes: list[str] | None = None,
    keywords: list[str] | None = None,
) -> tuple[Story, Dataset, object]:
    active_dataset = _require_active_dataset(session)
    _assert_dataset_version(active_dataset, expected_dataset_version)

    story = Story(
        dataset_id=active_dataset.id,
        fields_json=_normalize_story_fields(fields),
        row_hash="",
    )
    session.add(story)
    session.flush()

    affected_trope_ids: set[str] = set()
    affected_keyword_ids: set[str] = set()

    for position, trope_text in enumerate(_normalize_term_list(tropes)):
        trope = _resolve_trope(session, text=trope_text)
        story.trope_links.append(
            StoryTrope(
                trope=trope,
                origin=StoryTropeOrigin.HUMAN_ENTERED,
                status=AssignmentStatus.VALIDATED,
                position=position,
            )
        )
        affected_trope_ids.add(trope.id)

    for position, keyword_text in enumerate(_normalize_term_list(keywords)):
        keyword = _resolve_keyword(session, text=keyword_text)
        story.keyword_links.append(
            StoryKeyword(
                keyword=keyword,
                position=position,
            )
        )
        affected_keyword_ids.add(keyword.id)

    session.flush()

    for trope_id in affected_trope_ids:
        _refresh_trope_cached_story_count(session, active_dataset.id, trope_id)
    for keyword_id in affected_keyword_ids:
        _refresh_keyword_cached_story_count(session, active_dataset.id, keyword_id)

    sync_story_derived_fields(story)
    active_dataset.version += 1
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_created",
    )
    session.commit()
    return story, active_dataset, job


def add_story_trope(
    session: Session,
    story_id: str,
    *,
    expected_story_version: int,
    trope_id: str | None = None,
    text: str | None = None,
    origin: StoryTropeOrigin = StoryTropeOrigin.HUMAN_ENTERED,
) -> tuple[Story, Dataset, StoryTrope, object]:
    if origin not in {StoryTropeOrigin.HUMAN_ENTERED, StoryTropeOrigin.SEMANTIC_SUGGESTION}:
        raise StoryMutationValidationError("New trope assignments may be human_entered or semantic_suggestion only.")

    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    trope = _resolve_trope(session, trope_id=trope_id, text=text)
    if any(link.trope_id == trope.id for link in story.trope_links):
        raise StoryMutationValidationError("Story already has this trope assignment.")

    link = StoryTrope(
        trope=trope,
        origin=origin,
        status=AssignmentStatus.PENDING if origin == StoryTropeOrigin.SEMANTIC_SUGGESTION else AssignmentStatus.VALIDATED,
        position=_next_trope_position(story),
    )
    story.trope_links.append(link)
    session.flush()

    _refresh_trope_cached_story_count(session, active_dataset.id, trope.id)
    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_trope_added",
        trope_id=trope.id,
    )
    session.commit()
    return story, active_dataset, link, job


def delete_story_trope(
    session: Session,
    story_id: str,
    trope_id: str,
    *,
    expected_story_version: int,
) -> tuple[Story, Dataset, str, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    link = next((item for item in story.trope_links if item.trope_id == trope_id), None)
    if link is None:
        raise StoryTropeNotFoundError("Trope assignment not found on this story.")

    removed_trope_id = link.trope_id
    story.trope_links.remove(link)
    session.flush()

    _refresh_trope_cached_story_count(session, active_dataset.id, removed_trope_id)
    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_trope_deleted",
        trope_id=removed_trope_id,
    )
    session.commit()
    return story, active_dataset, removed_trope_id, job


def validate_story_trope(
    session: Session,
    story_id: str,
    trope_id: str,
    *,
    expected_story_version: int,
) -> tuple[Story, Dataset, StoryTrope, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    link = next((item for item in story.trope_links if item.trope_id == trope_id), None)
    if link is None:
        raise StoryTropeNotFoundError("Trope assignment not found on this story.")
    if link.status == AssignmentStatus.VALIDATED and link.origin != StoryTropeOrigin.SEMANTIC_SUGGESTION:
        raise StoryMutationValidationError("Trope assignment is already validated.")

    link.status = AssignmentStatus.VALIDATED
    if link.origin == StoryTropeOrigin.SEMANTIC_SUGGESTION:
        link.origin = StoryTropeOrigin.HUMAN_APPROVED

    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_trope_validated",
        trope_id=trope_id,
    )
    session.commit()
    return story, active_dataset, link, job


def _get_active_dataset(session: Session) -> Dataset | None:
    return session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))


def _require_active_dataset(session: Session) -> Dataset:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise ActiveDatasetNotFoundError("No active dataset is available.")
    return active_dataset


def _get_active_story(session: Session, story_id: str) -> tuple[Dataset, Story]:
    active_dataset = _require_active_dataset(session)

    story = session.scalar(
        select(Story)
        .where(
            Story.id == story_id,
            Story.dataset_id == active_dataset.id,
        )
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
        )
    )
    if story is None:
        raise StoryNotFoundError("Story not found in the active dataset.")
    return active_dataset, story


def _assert_story_version(story: Story, expected_story_version: int) -> None:
    if story.version != expected_story_version:
        raise StoryVersionConflictError(story.version)


def _assert_dataset_version(dataset: Dataset, expected_dataset_version: int) -> None:
    if dataset.version != expected_dataset_version:
        raise DatasetVersionConflictError(dataset.version)


def _normalize_story_fields(fields: Mapping[str, object] | None) -> dict[str, str]:
    values = fields or {}
    return {column: clean_text(values.get(column, "")) for column in CSV_COLUMNS}


def _normalize_term_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return dedupe_preserve_order(values)


def _resolve_trope(session: Session, *, trope_id: str | None = None, text: str | None = None) -> Trope:
    has_trope_id = bool(clean_text(trope_id))
    has_text = bool(clean_text(text))
    if has_trope_id == has_text:
        raise StoryMutationValidationError("Provide exactly one of trope_id or text when assigning a trope.")

    if has_trope_id:
        trope = session.get(Trope, clean_text(trope_id))
        if trope is None:
            raise TropeNotFoundError("Canonical trope not found.")
        return trope

    trope_text = clean_text(text)
    marker = normalize_text(trope_text)
    if not marker:
        raise StoryMutationValidationError("Trope text cannot be empty.")

    trope = session.scalar(select(Trope).where(Trope.normalized_text == marker))
    if trope is not None:
        return trope

    trope = Trope(text=trope_text)
    session.add(trope)
    session.flush()
    return trope


def _resolve_keyword(session: Session, *, text: str) -> Keyword:
    keyword_text = clean_text(text)
    marker = normalize_text(keyword_text)
    if not marker:
        raise StoryMutationValidationError("Keyword text cannot be empty.")

    keyword = session.scalar(select(Keyword).where(Keyword.normalized_text == marker))
    if keyword is not None:
        return keyword

    keyword = Keyword(text=keyword_text)
    session.add(keyword)
    session.flush()
    return keyword


def _queue_story_rebuild(
    session: Session,
    *,
    dataset_id: str,
    story_id: str,
    reason: str,
    trope_id: str | None = None,
):
    payload = {
        "reason": reason,
        "story_id": story_id,
    }
    if trope_id is not None:
        payload["trope_id"] = trope_id
    return queue_job(
        session,
        job_type="full_rebuild",
        dataset_id=dataset_id,
        payload=payload,
    )


def _increment_versions(story: Story, dataset: Dataset) -> None:
    story.version += 1
    dataset.version += 1


def _next_trope_position(story: Story) -> int:
    positions = [link.position for link in story.trope_links if link.position is not None]
    return (max(positions) + 1) if positions else 0


def _refresh_trope_cached_story_count(session: Session, dataset_id: str, trope_id: str) -> None:
    trope = session.get(Trope, trope_id)
    if trope is None:
        return
    count = (
        session.scalar(
            select(func.count(func.distinct(Story.id)))
            .select_from(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(
                Story.dataset_id == dataset_id,
                StoryTrope.trope_id == trope_id,
            )
        )
        or 0
    )
    trope.cached_story_count = int(count)


def _refresh_keyword_cached_story_count(session: Session, dataset_id: str, keyword_id: str) -> None:
    keyword = session.get(Keyword, keyword_id)
    if keyword is None:
        return
    count = (
        session.scalar(
            select(func.count(func.distinct(Story.id)))
            .select_from(StoryKeyword)
            .join(Story, Story.id == StoryKeyword.story_id)
            .where(
                Story.dataset_id == dataset_id,
                StoryKeyword.keyword_id == keyword_id,
            )
        )
        or 0
    )
    keyword.cached_story_count = int(count)


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


def _ordered_keyword_links(story: Story) -> list[StoryKeyword]:
    return sorted(
        [link for link in story.keyword_links if link.keyword is not None],
        key=lambda item: (
            item.position is None,
            item.position if item.position is not None else 0,
            item.created_at,
            item.keyword.text if item.keyword is not None else "",
        ),
    )


def _build_story_fields(story: Story) -> dict[str, str]:
    row = {column: clean_text((story.fields_json or {}).get(column, "")) for column in CSV_COLUMNS}
    row[TROPE_FIELD] = serialize_tropes([link.trope.text for link in _ordered_trope_links(story)])
    row[KEYWORD_FIELD] = serialize_keywords([link.keyword.text for link in _ordered_keyword_links(story)])
    return row


def sync_story_derived_fields(story: Story) -> None:
    fields = _build_story_fields(story)
    story.fields_json = fields
    story.row_hash = _row_hash(fields)


def _row_hash(fields: dict[str, str]) -> str:
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serialize_story_summary(story: Story) -> dict:
    fields = _build_story_fields(story)
    return {
        "id": story.id,
        "dataset_id": story.dataset_id,
        "source_row_number": story.source_row_number,
        "version": story.version,
        "title": fields.get("Story title (Eng)", ""),
        "territory": fields.get("territory", ""),
        "summary": fields.get("1-sentence summary", ""),
        "trope_count": len(_ordered_trope_links(story)),
        "keyword_count": len(_ordered_keyword_links(story)),
    }


def _serialize_story_detail(story: Story) -> dict:
    return {
        "id": story.id,
        "dataset_id": story.dataset_id,
        "source_row_number": story.source_row_number,
        "version": story.version,
        "created_at": story.created_at.isoformat(),
        "updated_at": story.updated_at.isoformat(),
        "fields": _build_story_fields(story),
        "tropes": [_serialize_story_trope(link) for link in _ordered_trope_links(story)],
        "keywords": [_serialize_story_keyword(link) for link in _ordered_keyword_links(story)],
    }


def _serialize_story_trope(link: StoryTrope) -> dict:
    return {
        "id": link.trope.id,
        "text": link.trope.text,
        "story_count": int(link.trope.cached_story_count or 0),
        "origin": link.origin.value,
        "status": link.status.value,
        "position": link.position,
    }


def _serialize_story_keyword(link: StoryKeyword) -> dict:
    return {
        "id": link.keyword.id,
        "text": link.keyword.text,
        "position": link.position,
    }
