from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.coordinates import parse_space_coord
from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.core.parsing import clean_text, dedupe_preserve_order, normalize_text, serialize_keywords, serialize_tropes
from app.db.models import (
    AssignmentStatus,
    Dataset,
    DatasetStatus,
    Keyword,
    ReviewType,
    Story,
    StoryKeyword,
    StoryTrope,
    StoryTropeOrigin,
    TermKind,
    TermReviewStatus,
    Trope,
    UserRole,
)
from app.services.audit import record_audit_event
from app.services.jobs import queue_job
from app.services.reviews import (
    queue_story_field_review_item,
    queue_story_keyword_review_item,
    queue_story_trope_review_item,
    queue_term_review_item,
)


class StoryServiceError(ValueError):
    """Base error for story review operations."""


class StoryNotFoundError(StoryServiceError):
    """Raised when a story is not in the active dataset."""


class StoryTropeNotFoundError(StoryServiceError):
    """Raised when a story-trope assignment cannot be found."""


class StoryKeywordNotFoundError(StoryServiceError):
    """Raised when a story-keyword assignment cannot be found."""


class TropeNotFoundError(StoryServiceError):
    """Raised when a canonical trope cannot be found."""


class KeywordNotFoundError(StoryServiceError):
    """Raised when a canonical keyword cannot be found."""


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


def get_story_keywords(session: Session, story_id: str) -> dict:
    _, story = _get_active_story(session, story_id)
    return {
        "story_id": story.id,
        "story_version": story.version,
        "items": [_serialize_story_keyword(link) for link in _ordered_keyword_links(story)],
    }


def create_story(
    session: Session,
    *,
    expected_dataset_version: int,
    fields: Mapping[str, object] | None = None,
    tropes: list[str] | None = None,
    keywords: list[str] | None = None,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
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
    newly_created_tropes: list[Trope] = []
    newly_created_keywords: list[Keyword] = []

    for position, trope_text in enumerate(_normalize_term_list(tropes)):
        trope, created = _resolve_trope(
            session,
            active_dataset.id,
            text=trope_text,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )
        story.trope_links.append(
            StoryTrope(
                trope=trope,
                origin=StoryTropeOrigin.HUMAN_ENTERED,
                status=AssignmentStatus.VALIDATED,
                position=position,
            )
        )
        affected_trope_ids.add(trope.id)
        if created:
            newly_created_tropes.append(trope)

    for position, keyword_text in enumerate(_normalize_term_list(keywords)):
        keyword, created = _resolve_keyword(
            session,
            active_dataset.id,
            text=keyword_text,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )
        story.keyword_links.append(
            StoryKeyword(
                keyword=keyword,
                position=position,
            )
        )
        affected_keyword_ids.add(keyword.id)
        if created:
            newly_created_keywords.append(keyword)

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
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        for column in CSV_COLUMNS:
            if column in {TROPE_FIELD, KEYWORD_FIELD}:
                continue
            value = clean_text((story.fields_json or {}).get(column, ""))
            if not value:
                continue
            queue_story_field_review_item(
                session,
                dataset_id=active_dataset.id,
                story=story,
                actor_user_id=actor_user_id,
                review_type=ReviewType.STORY_CREATED,
                field_name=column,
                previous_value="",
                current_value=value,
            )
        for link in _ordered_trope_links(story):
            queue_story_trope_review_item(
                session,
                dataset_id=active_dataset.id,
                story=story,
                actor_user_id=actor_user_id,
                review_type=ReviewType.STORY_CREATED,
                assignment_action="added",
                current_trope=_serialize_story_trope(link),
                position=link.position,
            )
        for link in _ordered_keyword_links(story):
            queue_story_keyword_review_item(
                session,
                dataset_id=active_dataset.id,
                story=story,
                actor_user_id=actor_user_id,
                review_type=ReviewType.STORY_CREATED,
                assignment_action="added",
                current_keyword=_serialize_story_keyword(link),
                position=link.position,
            )
    _queue_pending_term_reviews(
        session,
        dataset_id=active_dataset.id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tropes=newly_created_tropes,
        keywords=newly_created_keywords,
    )
    record_audit_event(
        session,
        event_type="story.created",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "trope_count": len(story.trope_links),
            "keyword_count": len(story.keyword_links),
        },
    )
    session.commit()
    return story, active_dataset, job


def update_story(
    session: Session,
    story_id: str,
    *,
    expected_story_version: int,
    fields: Mapping[str, object] | None = None,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    field_updates = _normalize_story_field_updates(fields)
    if not field_updates:
        raise StoryMutationValidationError("Provide at least one editable story field to update.")

    story_fields = _build_story_fields(story)
    previous_field_values = {column: story_fields.get(column, "") for column in field_updates}
    story_fields.update(field_updates)
    story.fields_json = story_fields
    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_updated",
    )
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        for column, previous_value in previous_field_values.items():
            queue_story_field_review_item(
                session,
                dataset_id=active_dataset.id,
                story=story,
                actor_user_id=actor_user_id,
                review_type=ReviewType.STORY_UPDATED,
                field_name=column,
                previous_value=previous_value,
                current_value=story_fields.get(column, ""),
            )
    record_audit_event(
        session,
        event_type="story.updated",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "updated_fields": sorted(_normalize_story_field_updates(fields).keys()),
        },
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
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, StoryTrope, object]:
    if origin not in {StoryTropeOrigin.HUMAN_ENTERED, StoryTropeOrigin.SEMANTIC_SUGGESTION}:
        raise StoryMutationValidationError("New trope assignments may be human_entered or semantic_suggestion only.")

    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    trope, created = _resolve_trope(
        session,
        active_dataset.id,
        trope_id=trope_id,
        text=text,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
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
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        queue_story_trope_review_item(
            session,
            dataset_id=active_dataset.id,
            story=story,
            actor_user_id=actor_user_id,
            review_type=ReviewType.STORY_UPDATED,
            assignment_action="added",
            current_trope=_serialize_story_trope(link),
            position=link.position,
        )
    _queue_pending_term_reviews(
        session,
        dataset_id=active_dataset.id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tropes=[trope] if created else [],
        keywords=[],
    )
    record_audit_event(
        session,
        event_type="story.trope_added",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "trope_id": trope.id,
            "origin": link.origin.value,
            "status": link.status.value,
        },
    )
    session.commit()
    return story, active_dataset, link, job


def add_story_keyword(
    session: Session,
    story_id: str,
    *,
    expected_story_version: int,
    keyword_id: str | None = None,
    text: str | None = None,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, StoryKeyword, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    keyword, created = _resolve_keyword(
        session,
        active_dataset.id,
        keyword_id=keyword_id,
        text=text,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
    if any(link.keyword_id == keyword.id for link in story.keyword_links):
        raise StoryMutationValidationError("Story already has this keyword assignment.")

    link = StoryKeyword(
        keyword=keyword,
        position=_next_keyword_position(story),
    )
    story.keyword_links.append(link)
    session.flush()

    _refresh_keyword_cached_story_count(session, active_dataset.id, keyword.id)
    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_keyword_added",
        keyword_id=keyword.id,
    )
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        queue_story_keyword_review_item(
            session,
            dataset_id=active_dataset.id,
            story=story,
            actor_user_id=actor_user_id,
            review_type=ReviewType.STORY_UPDATED,
            assignment_action="added",
            current_keyword=_serialize_story_keyword(link),
            position=link.position,
        )
    _queue_pending_term_reviews(
        session,
        dataset_id=active_dataset.id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tropes=[],
        keywords=[keyword] if created else [],
    )
    record_audit_event(
        session,
        event_type="story.keyword_added",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "keyword_id": keyword.id,
        },
    )
    session.commit()
    return story, active_dataset, link, job


def replace_story_trope(
    session: Session,
    story_id: str,
    current_trope_id: str,
    *,
    expected_story_version: int,
    trope_id: str | None = None,
    text: str | None = None,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, StoryTrope, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    link = next((item for item in story.trope_links if item.trope_id == current_trope_id), None)
    if link is None:
        raise StoryTropeNotFoundError("Trope assignment not found on this story.")

    replacement_trope, created = _resolve_trope(
        session,
        active_dataset.id,
        trope_id=trope_id,
        text=text,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
    if replacement_trope.id == current_trope_id:
        raise StoryMutationValidationError("Edited trope matches the current trope assignment.")
    if any(item.trope_id == replacement_trope.id for item in story.trope_links if item.trope_id != current_trope_id):
        raise StoryMutationValidationError("Story already has this trope assignment.")

    previous_trope_id = link.trope_id
    previous_position = link.position
    previous_trope_snapshot = _serialize_story_trope(link)
    story.trope_links.remove(link)
    session.flush()

    replacement_link = StoryTrope(
        trope=replacement_trope,
        origin=StoryTropeOrigin.HUMAN_ENTERED,
        status=AssignmentStatus.VALIDATED,
        position=previous_position,
    )
    story.trope_links.append(replacement_link)
    session.flush()

    _refresh_trope_cached_story_count(session, active_dataset.id, previous_trope_id)
    _refresh_trope_cached_story_count(session, active_dataset.id, replacement_trope.id)
    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_trope_replaced",
        trope_id=replacement_trope.id,
    )
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        queue_story_trope_review_item(
            session,
            dataset_id=active_dataset.id,
            story=story,
            actor_user_id=actor_user_id,
            review_type=ReviewType.STORY_UPDATED,
            assignment_action="replaced",
            previous_trope=previous_trope_snapshot,
            current_trope=_serialize_story_trope(replacement_link),
            position=previous_position,
        )
    _queue_pending_term_reviews(
        session,
        dataset_id=active_dataset.id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tropes=[replacement_trope] if created else [],
        keywords=[],
    )
    record_audit_event(
        session,
        event_type="story.trope_replaced",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "previous_trope_id": previous_trope_id,
            "trope_id": replacement_trope.id,
        },
    )
    session.commit()
    return story, active_dataset, replacement_link, job


def replace_story_keyword(
    session: Session,
    story_id: str,
    current_keyword_id: str,
    *,
    expected_story_version: int,
    keyword_id: str | None = None,
    text: str | None = None,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, StoryKeyword, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    link = next((item for item in story.keyword_links if item.keyword_id == current_keyword_id), None)
    if link is None:
        raise StoryKeywordNotFoundError("Keyword assignment not found on this story.")

    replacement_keyword, created = _resolve_keyword(
        session,
        active_dataset.id,
        keyword_id=keyword_id,
        text=text,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
    if replacement_keyword.id == current_keyword_id:
        raise StoryMutationValidationError("Edited keyword matches the current keyword assignment.")
    if any(item.keyword_id == replacement_keyword.id for item in story.keyword_links if item.keyword_id != current_keyword_id):
        raise StoryMutationValidationError("Story already has this keyword assignment.")

    previous_keyword_id = link.keyword_id
    previous_position = link.position
    previous_keyword_snapshot = _serialize_story_keyword(link)
    story.keyword_links.remove(link)
    session.flush()

    replacement_link = StoryKeyword(
        keyword=replacement_keyword,
        position=previous_position,
    )
    story.keyword_links.append(replacement_link)
    session.flush()

    _refresh_keyword_cached_story_count(session, active_dataset.id, previous_keyword_id)
    _refresh_keyword_cached_story_count(session, active_dataset.id, replacement_keyword.id)
    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_keyword_replaced",
        keyword_id=replacement_keyword.id,
    )
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        queue_story_keyword_review_item(
            session,
            dataset_id=active_dataset.id,
            story=story,
            actor_user_id=actor_user_id,
            review_type=ReviewType.STORY_UPDATED,
            assignment_action="replaced",
            previous_keyword=previous_keyword_snapshot,
            current_keyword=_serialize_story_keyword(replacement_link),
            position=previous_position,
        )
    _queue_pending_term_reviews(
        session,
        dataset_id=active_dataset.id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tropes=[],
        keywords=[replacement_keyword] if created else [],
    )
    record_audit_event(
        session,
        event_type="story.keyword_replaced",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "previous_keyword_id": previous_keyword_id,
            "keyword_id": replacement_keyword.id,
        },
    )
    session.commit()
    return story, active_dataset, replacement_link, job


def delete_story_trope(
    session: Session,
    story_id: str,
    trope_id: str,
    *,
    expected_story_version: int,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, str, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    link = next((item for item in story.trope_links if item.trope_id == trope_id), None)
    if link is None:
        raise StoryTropeNotFoundError("Trope assignment not found on this story.")

    removed_trope_id = link.trope_id
    removed_trope_snapshot = _serialize_story_trope(link)
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
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        queue_story_trope_review_item(
            session,
            dataset_id=active_dataset.id,
            story=story,
            actor_user_id=actor_user_id,
            review_type=ReviewType.STORY_UPDATED,
            assignment_action="deleted",
            previous_trope=removed_trope_snapshot,
            position=link.position,
        )
    record_audit_event(
        session,
        event_type="story.trope_deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "trope_id": removed_trope_id,
        },
    )
    session.commit()
    return story, active_dataset, removed_trope_id, job


def delete_story_keyword(
    session: Session,
    story_id: str,
    keyword_id: str,
    *,
    expected_story_version: int,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, str, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    link = next((item for item in story.keyword_links if item.keyword_id == keyword_id), None)
    if link is None:
        raise StoryKeywordNotFoundError("Keyword assignment not found on this story.")

    removed_keyword_id = link.keyword_id
    removed_keyword_snapshot = _serialize_story_keyword(link)
    story.keyword_links.remove(link)
    session.flush()

    _refresh_keyword_cached_story_count(session, active_dataset.id, removed_keyword_id)
    sync_story_derived_fields(story)
    _increment_versions(story, active_dataset)
    job = _queue_story_rebuild(
        session,
        dataset_id=active_dataset.id,
        story_id=story.id,
        reason="story_keyword_deleted",
        keyword_id=removed_keyword_id,
    )
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        queue_story_keyword_review_item(
            session,
            dataset_id=active_dataset.id,
            story=story,
            actor_user_id=actor_user_id,
            review_type=ReviewType.STORY_UPDATED,
            assignment_action="deleted",
            previous_keyword=removed_keyword_snapshot,
            position=link.position,
        )
    record_audit_event(
        session,
        event_type="story.keyword_deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "keyword_id": removed_keyword_id,
        },
    )
    session.commit()
    return story, active_dataset, removed_keyword_id, job


def validate_story_trope(
    session: Session,
    story_id: str,
    trope_id: str,
    *,
    expected_story_version: int,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Story, Dataset, StoryTrope, object]:
    active_dataset, story = _get_active_story(session, story_id)
    _assert_story_version(story, expected_story_version)

    link = next((item for item in story.trope_links if item.trope_id == trope_id), None)
    if link is None:
        raise StoryTropeNotFoundError("Trope assignment not found on this story.")
    if link.status == AssignmentStatus.VALIDATED and link.origin != StoryTropeOrigin.SEMANTIC_SUGGESTION:
        raise StoryMutationValidationError("Trope assignment is already validated.")

    previous_trope_snapshot = _serialize_story_trope(link)
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
    if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
        queue_story_trope_review_item(
            session,
            dataset_id=active_dataset.id,
            story=story,
            actor_user_id=actor_user_id,
            review_type=ReviewType.STORY_UPDATED,
            assignment_action="validated",
            previous_trope=previous_trope_snapshot,
            current_trope=_serialize_story_trope(link),
            position=link.position,
        )
    record_audit_event(
        session,
        event_type="story.trope_validated",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="stories",
        subject_id=story.id,
        payload={
            "story_version": story.version,
            "dataset_version": active_dataset.version,
            "trope_id": trope_id,
            "origin": link.origin.value,
            "status": link.status.value,
        },
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


def _normalize_story_field_updates(fields: Mapping[str, object] | None) -> dict[str, str]:
    if not fields:
        return {}
    editable_columns = {
        column
        for column in CSV_COLUMNS
        if column not in {TROPE_FIELD, KEYWORD_FIELD}
    }
    return {
        column: clean_text(value)
        for column, value in fields.items()
        if column in editable_columns
    }


def _normalize_term_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return dedupe_preserve_order(values)


def _resolve_trope(
    session: Session,
    dataset_id: str,
    *,
    trope_id: str | None = None,
    text: str | None = None,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Trope, bool]:
    has_trope_id = bool(clean_text(trope_id))
    has_text = bool(clean_text(text))
    if has_trope_id == has_text:
        raise StoryMutationValidationError("Provide exactly one of trope_id or text when assigning a trope.")

    if has_trope_id:
        trope = session.scalar(
            select(Trope).where(
                Trope.id == clean_text(trope_id),
                Trope.dataset_id == dataset_id,
            )
        )
        if trope is None:
            raise TropeNotFoundError("Canonical trope not found.")
        return trope, False

    trope_text = clean_text(text)
    marker = normalize_text(trope_text)
    if not marker:
        raise StoryMutationValidationError("Trope text cannot be empty.")

    trope = session.scalar(
        select(Trope).where(
            Trope.dataset_id == dataset_id,
            Trope.normalized_text == marker,
        )
    )
    if trope is not None:
        return trope, False

    trope = Trope(
        dataset_id=dataset_id,
        text=trope_text,
        review_status=TermReviewStatus.PENDING_REVIEW if actor_role == UserRole.CONTRIBUTOR else TermReviewStatus.APPROVED,
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    session.add(trope)
    session.flush()
    return trope, True


def _resolve_keyword(
    session: Session,
    dataset_id: str,
    *,
    keyword_id: str | None = None,
    text: str | None = None,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[Keyword, bool]:
    has_keyword_id = bool(clean_text(keyword_id))
    has_text = bool(clean_text(text))
    if has_keyword_id == has_text:
        raise StoryMutationValidationError("Provide exactly one of keyword_id or text when assigning a keyword.")

    if has_keyword_id:
        keyword = session.scalar(
            select(Keyword).where(
                Keyword.id == clean_text(keyword_id),
                Keyword.dataset_id == dataset_id,
            )
        )
        if keyword is None:
            raise KeywordNotFoundError("Canonical keyword not found.")
        return keyword, False

    keyword_text = clean_text(text)
    marker = normalize_text(keyword_text)
    if not marker:
        raise StoryMutationValidationError("Keyword text cannot be empty.")

    keyword = session.scalar(
        select(Keyword).where(
            Keyword.dataset_id == dataset_id,
            Keyword.normalized_text == marker,
        )
    )
    if keyword is not None:
        return keyword, False

    keyword = Keyword(
        dataset_id=dataset_id,
        text=keyword_text,
        review_status=TermReviewStatus.PENDING_REVIEW if actor_role == UserRole.CONTRIBUTOR else TermReviewStatus.APPROVED,
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    session.add(keyword)
    session.flush()
    return keyword, True


def _queue_story_rebuild(
    session: Session,
    *,
    dataset_id: str,
    story_id: str,
    reason: str,
    trope_id: str | None = None,
    keyword_id: str | None = None,
):
    payload = {
        "reason": reason,
        "story_id": story_id,
    }
    if trope_id is not None:
        payload["trope_id"] = trope_id
    if keyword_id is not None:
        payload["keyword_id"] = keyword_id
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


def _next_keyword_position(story: Story) -> int:
    positions = [link.position for link in story.keyword_links if link.position is not None]
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
        "has_location": parse_space_coord(fields.get("space coord", "")) is not None,
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


def _queue_pending_term_reviews(
    session: Session,
    *,
    dataset_id: str,
    actor_user_id: str | None,
    actor_role: UserRole | None,
    tropes: list[Trope],
    keywords: list[Keyword],
) -> None:
    if actor_role != UserRole.CONTRIBUTOR or not actor_user_id:
        return
    for trope in tropes:
        record_audit_event(
            session,
            event_type="trope.created",
            actor_user_id=actor_user_id,
            dataset_id=dataset_id,
            subject_table="tropes",
            subject_id=trope.id,
            payload={
                "created": True,
                "review_status": trope.review_status.value,
            },
        )
        queue_term_review_item(
            session,
            dataset_id=dataset_id,
            term_kind=TermKind.TROPE,
            subject_id=trope.id,
            actor_user_id=actor_user_id,
            text=trope.text,
        )
    for keyword in keywords:
        record_audit_event(
            session,
            event_type="keyword.created",
            actor_user_id=actor_user_id,
            dataset_id=dataset_id,
            subject_table="keywords",
            subject_id=keyword.id,
            payload={
                "created": True,
                "review_status": keyword.review_status.value,
            },
        )
        queue_term_review_item(
            session,
            dataset_id=dataset_id,
            term_kind=TermKind.KEYWORD,
            subject_id=keyword.id,
            actor_user_id=actor_user_id,
            text=keyword.text,
        )
