from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.parsing import clean_text
from app.db.models import (
    AssignmentStatus,
    Dataset,
    Keyword,
    ReviewItem,
    ReviewStatus,
    ReviewType,
    Story,
    StoryKeyword,
    StoryTrope,
    StoryTropeOrigin,
    TermEmbedding,
    TermKind,
    TermReviewStatus,
    TermSimilarityCache,
    Trope,
)
from app.services.audit import record_audit_event

STORY_FIELD_CHANGE_KIND = "story_field"
STORY_TROPE_CHANGE_KIND = "story_trope"
STORY_KEYWORD_CHANGE_KIND = "story_keyword"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReviewError(ValueError):
    """Base review workflow error."""


class ReviewNotFoundError(ReviewError):
    """Raised when a review item cannot be found."""


class ReviewConflictError(ReviewError):
    """Raised when a review item cannot be resolved in its current state."""


class ReviewValidationError(ReviewError):
    """Raised when a review request is invalid."""


def queue_story_field_review_item(
    session: Session,
    *,
    dataset_id: str,
    story: Story,
    actor_user_id: str,
    review_type: ReviewType,
    field_name: str,
    previous_value: str,
    current_value: str,
) -> ReviewItem | None:
    previous_marker = clean_text(previous_value)
    current_marker = clean_text(current_value)
    if previous_marker == current_marker:
        return None

    metadata = {
        "story_id": story.id,
        "story_title": clean_text((story.fields_json or {}).get("Story title (Eng)", "")),
        "story_version": story.version,
        "source_row_number": story.source_row_number,
        "change_kind": STORY_FIELD_CHANGE_KIND,
        "field_name": field_name,
        "previous_value": previous_marker,
        "current_value": current_marker,
    }
    return _create_review_item(
        session,
        dataset_id=dataset_id,
        review_type=review_type,
        subject_table="stories",
        subject_id=story.id,
        actor_user_id=actor_user_id,
        metadata=metadata,
    )


def queue_story_trope_review_item(
    session: Session,
    *,
    dataset_id: str,
    story: Story,
    actor_user_id: str,
    review_type: ReviewType,
    assignment_action: str,
    previous_trope: dict[str, object] | None = None,
    current_trope: dict[str, object] | None = None,
    position: int | None = None,
) -> ReviewItem:
    metadata = {
        "story_id": story.id,
        "story_title": clean_text((story.fields_json or {}).get("Story title (Eng)", "")),
        "story_version": story.version,
        "source_row_number": story.source_row_number,
        "change_kind": STORY_TROPE_CHANGE_KIND,
        "assignment_action": assignment_action,
        "position": position,
    }
    if previous_trope is not None:
        metadata.update(
            {
                "previous_trope_id": clean_text(previous_trope.get("id", "")),
                "previous_trope_text": clean_text(previous_trope.get("text", "")),
                "previous_origin": clean_text(previous_trope.get("origin", "")),
                "previous_status": clean_text(previous_trope.get("status", "")),
            }
        )
    if current_trope is not None:
        metadata.update(
            {
                "current_trope_id": clean_text(current_trope.get("id", "")),
                "current_trope_text": clean_text(current_trope.get("text", "")),
                "current_origin": clean_text(current_trope.get("origin", "")),
                "current_status": clean_text(current_trope.get("status", "")),
            }
        )
    return _create_review_item(
        session,
        dataset_id=dataset_id,
        review_type=review_type,
        subject_table="stories",
        subject_id=story.id,
        actor_user_id=actor_user_id,
        metadata=metadata,
    )


def queue_story_keyword_review_item(
    session: Session,
    *,
    dataset_id: str,
    story: Story,
    actor_user_id: str,
    review_type: ReviewType,
    assignment_action: str,
    previous_keyword: dict[str, object] | None = None,
    current_keyword: dict[str, object] | None = None,
    position: int | None = None,
) -> ReviewItem:
    metadata = {
        "story_id": story.id,
        "story_title": clean_text((story.fields_json or {}).get("Story title (Eng)", "")),
        "story_version": story.version,
        "source_row_number": story.source_row_number,
        "change_kind": STORY_KEYWORD_CHANGE_KIND,
        "assignment_action": assignment_action,
        "position": position,
    }
    if previous_keyword is not None:
        metadata.update(
            {
                "previous_keyword_id": clean_text(previous_keyword.get("id", "")),
                "previous_keyword_text": clean_text(previous_keyword.get("text", "")),
            }
        )
    if current_keyword is not None:
        metadata.update(
            {
                "current_keyword_id": clean_text(current_keyword.get("id", "")),
                "current_keyword_text": clean_text(current_keyword.get("text", "")),
            }
        )
    return _create_review_item(
        session,
        dataset_id=dataset_id,
        review_type=review_type,
        subject_table="stories",
        subject_id=story.id,
        actor_user_id=actor_user_id,
        metadata=metadata,
    )


def queue_term_review_item(
    session: Session,
    *,
    dataset_id: str,
    term_kind: TermKind,
    subject_id: str,
    actor_user_id: str,
    text: str,
) -> ReviewItem:
    review_type = ReviewType.TROPE_PENDING if term_kind == TermKind.TROPE else ReviewType.KEYWORD_PENDING
    subject_table = "tropes" if term_kind == TermKind.TROPE else "keywords"
    review_item = _get_pending_review_item(session, subject_table=subject_table, subject_id=subject_id)
    metadata = {
        "term_kind": term_kind.value,
        "text": text,
    }
    if review_item is None:
        review_item = ReviewItem(
            dataset_id=dataset_id,
            review_type=review_type,
            subject_table=subject_table,
            subject_id=subject_id,
            status=ReviewStatus.PENDING,
            created_by_user_id=actor_user_id,
            metadata_json=metadata,
        )
        session.add(review_item)
        session.flush()
        return review_item

    review_item.metadata_json = {
        **(review_item.metadata_json or {}),
        **metadata,
    }
    session.flush()
    return review_item


def _create_review_item(
    session: Session,
    *,
    dataset_id: str | None,
    review_type: ReviewType,
    subject_table: str,
    subject_id: str,
    actor_user_id: str | None,
    metadata: dict[str, object],
) -> ReviewItem:
    review_item = ReviewItem(
        dataset_id=dataset_id,
        review_type=review_type,
        subject_table=subject_table,
        subject_id=subject_id,
        status=ReviewStatus.PENDING,
        created_by_user_id=actor_user_id,
        metadata_json=metadata,
    )
    session.add(review_item)
    session.flush()
    return review_item


def list_review_items(
    session: Session,
    *,
    status: ReviewStatus | None = None,
    limit: int = 100,
) -> list[dict]:
    statement = select(ReviewItem)
    if status is not None:
        statement = statement.where(ReviewItem.status == status)
    items = session.scalars(
        statement.order_by(
            case((ReviewItem.status == ReviewStatus.PENDING, 0), else_=1),
            ReviewItem.created_at.desc(),
            ReviewItem.id.desc(),
        ).limit(limit)
    ).all()
    return [_serialize_review_item(session, item) for item in items]


def get_review_item_detail(session: Session, review_id: str) -> dict:
    review_item = session.get(ReviewItem, review_id)
    if review_item is None:
        raise ReviewNotFoundError("Review item not found.")
    return _serialize_review_item(session, review_item)


def approve_review_item(
    session: Session,
    *,
    review_id: str,
    actor_user_id: str,
    note: str | None = None,
) -> dict:
    review_item = _require_pending_review_item(session, review_id)

    if review_item.review_type == ReviewType.TROPE_PENDING:
        trope = _require_trope(session, review_item)
        trope.review_status = TermReviewStatus.APPROVED
        trope.updated_by_user_id = actor_user_id
    elif review_item.review_type == ReviewType.KEYWORD_PENDING:
        keyword = _require_keyword(session, review_item)
        keyword.review_status = TermReviewStatus.APPROVED
        keyword.updated_by_user_id = actor_user_id

    resolution_note = clean_text(note)
    _resolve_review_item(
        review_item,
        actor_user_id=actor_user_id,
        status=ReviewStatus.APPROVED,
        resolution={
            "decision": "approved",
            "note": resolution_note or None,
        },
    )
    record_audit_event(
        session,
        event_type="review.approved",
        actor_user_id=actor_user_id,
        dataset_id=review_item.dataset_id,
        subject_table=review_item.subject_table,
        subject_id=review_item.subject_id,
        payload={"review_id": review_item.id, "note": resolution_note or None},
    )
    session.commit()
    return _serialize_review_item(session, review_item)


def reject_review_item(
    session: Session,
    *,
    review_id: str,
    actor_user_id: str,
    note: str | None = None,
    merge_target_id: str | None = None,
    remove_from_all_stories: bool = False,
) -> dict:
    review_item = _require_pending_review_item(session, review_id)
    resolution_note = clean_text(note) or None
    cleaned_merge_target_id = clean_text(merge_target_id) or None

    resolution_payload: dict[str, object] = {"decision": "rejected", "note": resolution_note}

    if review_item.review_type in {ReviewType.STORY_CREATED, ReviewType.STORY_UPDATED}:
        action = _reject_story_review_item(
            session,
            review_item=review_item,
        )
        resolution_payload.update(action)
    elif review_item.review_type == ReviewType.TROPE_PENDING:
        trope = _require_trope(session, review_item)
        action = _resolve_pending_trope_rejection(
            session,
            review_item=review_item,
            actor_user_id=actor_user_id,
            trope=trope,
            merge_target_id=cleaned_merge_target_id,
            remove_from_all_stories=remove_from_all_stories,
        )
        resolution_payload.update(action)
    elif review_item.review_type == ReviewType.KEYWORD_PENDING:
        keyword = _require_keyword(session, review_item)
        action = _resolve_pending_keyword_rejection(
            session,
            review_item=review_item,
            actor_user_id=actor_user_id,
            keyword=keyword,
            merge_target_id=cleaned_merge_target_id,
            remove_from_all_stories=remove_from_all_stories,
        )
        resolution_payload.update(action)
    else:
        raise ReviewValidationError("Unsupported review type.")

    _resolve_review_item(
        review_item,
        actor_user_id=actor_user_id,
        status=ReviewStatus.REJECTED,
        resolution=resolution_payload,
    )
    record_audit_event(
        session,
        event_type="review.rejected",
        actor_user_id=actor_user_id,
        dataset_id=review_item.dataset_id,
        subject_table=review_item.subject_table,
        subject_id=review_item.subject_id,
        payload={"review_id": review_item.id, **resolution_payload},
    )
    session.commit()
    return _serialize_review_item(session, review_item)


def _require_pending_review_item(session: Session, review_id: str) -> ReviewItem:
    review_item = session.get(ReviewItem, review_id)
    if review_item is None:
        raise ReviewNotFoundError("Review item not found.")
    if review_item.status != ReviewStatus.PENDING:
        raise ReviewConflictError("Review item is already resolved.")
    return review_item


def _reject_story_review_item(
    session: Session,
    *,
    review_item: ReviewItem,
) -> dict[str, object]:
    story = session.scalar(
        select(Story)
        .where(Story.id == review_item.subject_id)
        .options(
            selectinload(Story.dataset),
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
        )
    )
    if story is None:
        raise ReviewNotFoundError("Story review subject not found.")

    metadata = review_item.metadata_json or {}
    change_kind = clean_text(metadata.get("change_kind", ""))
    if change_kind == STORY_FIELD_CHANGE_KIND:
        return _reject_story_field_change(session, review_item=review_item, story=story)
    if change_kind == STORY_TROPE_CHANGE_KIND:
        return _reject_story_trope_change(session, review_item=review_item, story=story)
    if change_kind == STORY_KEYWORD_CHANGE_KIND:
        return _reject_story_keyword_change(session, review_item=review_item, story=story)
    raise ReviewValidationError("Unsupported story review item payload.")


def _get_pending_review_item(session: Session, *, subject_table: str, subject_id: str) -> ReviewItem | None:
    return session.scalar(
        select(ReviewItem)
        .where(
            ReviewItem.subject_table == subject_table,
            ReviewItem.subject_id == subject_id,
            ReviewItem.status == ReviewStatus.PENDING,
        )
        .order_by(ReviewItem.created_at.desc(), ReviewItem.id.desc())
    )


def _resolve_review_item(
    review_item: ReviewItem,
    *,
    actor_user_id: str,
    status: ReviewStatus,
    resolution: dict[str, object],
) -> None:
    review_item.status = status
    review_item.resolved_by_user_id = actor_user_id
    review_item.resolved_at = utc_now()
    review_item.metadata_json = {
        **(review_item.metadata_json or {}),
        "resolution": resolution,
    }


def _serialize_review_item(session: Session, review_item: ReviewItem) -> dict:
    return {
        "id": review_item.id,
        "dataset_id": review_item.dataset_id,
        "review_type": review_item.review_type.value,
        "subject_table": review_item.subject_table,
        "subject_id": review_item.subject_id,
        "status": review_item.status.value,
        "created_by_user_id": review_item.created_by_user_id,
        "resolved_by_user_id": review_item.resolved_by_user_id,
        "created_at": review_item.created_at.isoformat(),
        "updated_at": review_item.updated_at.isoformat(),
        "resolved_at": review_item.resolved_at.isoformat() if review_item.resolved_at else None,
        "metadata": review_item.metadata_json or {},
        "subject_preview": _subject_preview(session, review_item),
    }


def _subject_preview(session: Session, review_item: ReviewItem) -> dict | None:
    if review_item.subject_table == "stories":
        story = session.get(Story, review_item.subject_id)
        if story is None:
            return None
        fields = story.fields_json or {}
        return {
            "id": story.id,
            "title": clean_text(fields.get("Story title (Eng)", "")),
            "source_row_number": story.source_row_number,
            "version": story.version,
        }
    if review_item.subject_table == "tropes":
        trope = session.get(Trope, review_item.subject_id)
        if trope is None:
            return None
        return {
            "id": trope.id,
            "text": trope.text,
            "review_status": trope.review_status.value,
            "story_count": int(trope.cached_story_count or 0),
        }
    if review_item.subject_table == "keywords":
        keyword = session.get(Keyword, review_item.subject_id)
        if keyword is None:
            return None
        return {
            "id": keyword.id,
            "text": keyword.text,
            "review_status": keyword.review_status.value,
            "story_count": int(keyword.cached_story_count or 0),
        }
    return None


def _reject_story_field_change(
    session: Session,
    *,
    review_item: ReviewItem,
    story: Story,
) -> dict[str, object]:
    from app.services.stories import sync_story_derived_fields

    metadata = review_item.metadata_json or {}
    field_name = clean_text(metadata.get("field_name", ""))
    if not field_name:
        raise ReviewValidationError("Story field review item is missing field_name.")

    previous_value = clean_text(metadata.get("previous_value", ""))
    current_value = clean_text(metadata.get("current_value", ""))
    live_value = clean_text((story.fields_json or {}).get(field_name, ""))
    if live_value == previous_value:
        return {"action": "already_reverted", "field_name": field_name}
    if live_value != current_value:
        raise ReviewConflictError("Story field no longer matches the pending review change.")

    dataset = _require_dataset_match(review_item, story.dataset_id)
    story.fields_json = {
        **(story.fields_json or {}),
        field_name: previous_value,
    }
    sync_story_derived_fields(story)
    story.version += 1
    dataset.version += 1
    return {"action": "reverted", "field_name": field_name}


def _reject_story_trope_change(
    session: Session,
    *,
    review_item: ReviewItem,
    story: Story,
) -> dict[str, object]:
    from app.services.stories import sync_story_derived_fields

    metadata = review_item.metadata_json or {}
    assignment_action = clean_text(metadata.get("assignment_action", ""))
    dataset = _require_dataset_match(review_item, story.dataset_id)

    previous_trope_id = clean_text(metadata.get("previous_trope_id", ""))
    previous_trope_text = clean_text(metadata.get("previous_trope_text", ""))
    previous_origin = clean_text(metadata.get("previous_origin", ""))
    previous_status = clean_text(metadata.get("previous_status", ""))
    current_trope_id = clean_text(metadata.get("current_trope_id", ""))
    current_trope_text = clean_text(metadata.get("current_trope_text", ""))
    current_origin = clean_text(metadata.get("current_origin", ""))
    current_status = clean_text(metadata.get("current_status", ""))
    position = _metadata_position(metadata.get("position"))

    if assignment_action == "added":
        current_link = next((link for link in story.trope_links if link.trope_id == current_trope_id), None)
        if current_link is None:
            return {"action": "already_reverted", "assignment_action": assignment_action}
        session.delete(current_link)
        session.flush()
        _refresh_trope_cached_story_count(session, current_trope_id)
    elif assignment_action == "replaced":
        current_link = next((link for link in story.trope_links if link.trope_id == current_trope_id), None)
        if current_link is None:
            if any(link.trope_id == previous_trope_id for link in story.trope_links):
                return {"action": "already_reverted", "assignment_action": assignment_action}
            raise ReviewConflictError("Story trope replacement no longer matches the pending review change.")
        previous_trope = session.get(Trope, previous_trope_id)
        if previous_trope is None:
            raise ReviewNotFoundError("Original trope for the review item no longer exists.")
        if any(link.trope_id == previous_trope_id for link in story.trope_links if link.trope_id != current_trope_id):
            raise ReviewConflictError("Story already has the original trope assignment.")
        previous_link = StoryTrope(
            story_id=story.id,
            trope_id=previous_trope_id,
            origin=_coerce_story_trope_origin(previous_origin, fallback=current_link.origin),
            status=_coerce_assignment_status(previous_status, fallback=current_link.status),
            position=position if position is not None else current_link.position,
        )
        session.add(previous_link)
        session.delete(current_link)
        session.flush()
        _refresh_trope_cached_story_count(session, previous_trope_id)
        _refresh_trope_cached_story_count(session, current_trope_id)
    elif assignment_action == "deleted":
        if any(link.trope_id == previous_trope_id for link in story.trope_links):
            return {"action": "already_reverted", "assignment_action": assignment_action}
        previous_trope = session.get(Trope, previous_trope_id)
        if previous_trope is None:
            raise ReviewNotFoundError("Original trope for the review item no longer exists.")
        session.add(
            StoryTrope(
                story_id=story.id,
                trope_id=previous_trope_id,
                origin=_coerce_story_trope_origin(previous_origin, fallback=StoryTropeOrigin.HUMAN_ENTERED),
                status=_coerce_assignment_status(previous_status, fallback=AssignmentStatus.VALIDATED),
                position=position,
            )
        )
        session.flush()
        _refresh_trope_cached_story_count(session, previous_trope_id)
    elif assignment_action == "validated":
        current_link = next((link for link in story.trope_links if link.trope_id == current_trope_id), None)
        if current_link is None:
            raise ReviewConflictError("Story trope validation no longer matches the pending review change.")
        if (
            current_link.status.value == previous_status
            and current_link.origin.value == previous_origin
        ):
            return {"action": "already_reverted", "assignment_action": assignment_action}
        if (
            current_link.status.value != current_status
            or current_link.origin.value != current_origin
        ):
            raise ReviewConflictError("Story trope validation no longer matches the pending review change.")
        current_link.status = _coerce_assignment_status(previous_status, fallback=current_link.status)
        current_link.origin = _coerce_story_trope_origin(previous_origin, fallback=current_link.origin)
        session.flush()
    else:
        raise ReviewValidationError("Unsupported story trope review action.")

    sync_story_derived_fields(story)
    story.version += 1
    dataset.version += 1
    return {
        "action": "reverted",
        "assignment_action": assignment_action,
        "trope_text": current_trope_text or previous_trope_text,
    }


def _reject_story_keyword_change(
    session: Session,
    *,
    review_item: ReviewItem,
    story: Story,
) -> dict[str, object]:
    from app.services.stories import sync_story_derived_fields

    metadata = review_item.metadata_json or {}
    assignment_action = clean_text(metadata.get("assignment_action", ""))
    dataset = _require_dataset_match(review_item, story.dataset_id)

    previous_keyword_id = clean_text(metadata.get("previous_keyword_id", ""))
    previous_keyword_text = clean_text(metadata.get("previous_keyword_text", ""))
    current_keyword_id = clean_text(metadata.get("current_keyword_id", ""))
    current_keyword_text = clean_text(metadata.get("current_keyword_text", ""))
    position = _metadata_position(metadata.get("position"))

    if assignment_action == "added":
        current_link = next((link for link in story.keyword_links if link.keyword_id == current_keyword_id), None)
        if current_link is None:
            return {"action": "already_reverted", "assignment_action": assignment_action}
        session.delete(current_link)
        session.flush()
        _refresh_keyword_cached_story_count(session, current_keyword_id)
    elif assignment_action == "replaced":
        current_link = next((link for link in story.keyword_links if link.keyword_id == current_keyword_id), None)
        if current_link is None:
            if any(link.keyword_id == previous_keyword_id for link in story.keyword_links):
                return {"action": "already_reverted", "assignment_action": assignment_action}
            raise ReviewConflictError("Story keyword replacement no longer matches the pending review change.")
        previous_keyword = session.get(Keyword, previous_keyword_id)
        if previous_keyword is None:
            raise ReviewNotFoundError("Original keyword for the review item no longer exists.")
        if any(link.keyword_id == previous_keyword_id for link in story.keyword_links if link.keyword_id != current_keyword_id):
            raise ReviewConflictError("Story already has the original keyword assignment.")
        session.add(
            StoryKeyword(
                story_id=story.id,
                keyword_id=previous_keyword_id,
                position=position if position is not None else current_link.position,
            )
        )
        session.delete(current_link)
        session.flush()
        _refresh_keyword_cached_story_count(session, previous_keyword_id)
        _refresh_keyword_cached_story_count(session, current_keyword_id)
    elif assignment_action == "deleted":
        if any(link.keyword_id == previous_keyword_id for link in story.keyword_links):
            return {"action": "already_reverted", "assignment_action": assignment_action}
        previous_keyword = session.get(Keyword, previous_keyword_id)
        if previous_keyword is None:
            raise ReviewNotFoundError("Original keyword for the review item no longer exists.")
        session.add(
            StoryKeyword(
                story_id=story.id,
                keyword_id=previous_keyword_id,
                position=position,
            )
        )
        session.flush()
        _refresh_keyword_cached_story_count(session, previous_keyword_id)
    else:
        raise ReviewValidationError("Unsupported story keyword review action.")

    sync_story_derived_fields(story)
    story.version += 1
    dataset.version += 1
    return {
        "action": "reverted",
        "assignment_action": assignment_action,
        "keyword_text": current_keyword_text or previous_keyword_text,
    }


def _require_trope(session: Session, review_item: ReviewItem) -> Trope:
    trope = session.get(Trope, review_item.subject_id)
    if trope is None:
        raise ReviewNotFoundError("Pending trope not found.")
    return trope


def _require_keyword(session: Session, review_item: ReviewItem) -> Keyword:
    keyword = session.get(Keyword, review_item.subject_id)
    if keyword is None:
        raise ReviewNotFoundError("Pending keyword not found.")
    return keyword


def _resolve_pending_trope_rejection(
    session: Session,
    *,
    review_item: ReviewItem,
    actor_user_id: str,
    trope: Trope,
    merge_target_id: str | None,
    remove_from_all_stories: bool,
) -> dict[str, object]:
    active_dataset = _require_dataset_match(review_item, trope.dataset_id)
    if merge_target_id:
        target_trope = session.scalar(
            select(Trope).where(
                Trope.id == merge_target_id,
                Trope.dataset_id == active_dataset.id,
            )
        )
        if target_trope is None:
            raise ReviewValidationError("Merge target trope not found.")
        if target_trope.id == trope.id:
            raise ReviewValidationError("Merge target trope must be different from the pending trope.")

        affected_story_ids = _apply_trope_merge(
            session,
            dataset_id=active_dataset.id,
            source_trope_id=trope.id,
            target_trope_id=target_trope.id,
        )
        affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
        _refresh_trope_cached_story_count(session, target_trope.id)
        _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)
        record_audit_event(
            session,
            event_type="trope.merged",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="tropes",
            subject_id=trope.id,
            payload={"target_trope_id": target_trope.id, "review_id": review_item.id},
        )
        return {
            "action": "merged",
            "merge_target_id": target_trope.id,
            "affected_story_count": len(affected_story_ids),
        }

    if not remove_from_all_stories:
        raise ReviewValidationError(
            "Rejecting a pending trope requires merge_target_id or remove_from_all_stories=true."
        )

    affected_story_ids = _delete_trope_everywhere(session, dataset_id=active_dataset.id, trope_id=trope.id)
    affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
    _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)
    record_audit_event(
        session,
        event_type="trope.deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="tropes",
        subject_id=trope.id,
        payload={"remove_from_all_stories": True, "review_id": review_item.id},
    )
    return {
        "action": "deleted",
        "affected_story_count": len(affected_story_ids),
    }


def _resolve_pending_keyword_rejection(
    session: Session,
    *,
    review_item: ReviewItem,
    actor_user_id: str,
    keyword: Keyword,
    merge_target_id: str | None,
    remove_from_all_stories: bool,
) -> dict[str, object]:
    active_dataset = _require_dataset_match(review_item, keyword.dataset_id)
    if merge_target_id:
        target_keyword = session.scalar(
            select(Keyword).where(
                Keyword.id == merge_target_id,
                Keyword.dataset_id == active_dataset.id,
            )
        )
        if target_keyword is None:
            raise ReviewValidationError("Merge target keyword not found.")
        if target_keyword.id == keyword.id:
            raise ReviewValidationError("Merge target keyword must be different from the pending keyword.")

        affected_story_ids = _apply_keyword_merge(
            session,
            dataset_id=active_dataset.id,
            source_keyword_id=keyword.id,
            target_keyword_id=target_keyword.id,
        )
        affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
        _refresh_keyword_cached_story_count(session, target_keyword.id)
        _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)
        record_audit_event(
            session,
            event_type="keyword.merged",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="keywords",
            subject_id=keyword.id,
            payload={"target_keyword_id": target_keyword.id, "review_id": review_item.id},
        )
        return {
            "action": "merged",
            "merge_target_id": target_keyword.id,
            "affected_story_count": len(affected_story_ids),
        }

    if not remove_from_all_stories:
        raise ReviewValidationError(
            "Rejecting a pending keyword requires merge_target_id or remove_from_all_stories=true."
        )

    affected_story_ids = _delete_keyword_everywhere(session, dataset_id=active_dataset.id, keyword_id=keyword.id)
    affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
    _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)
    record_audit_event(
        session,
        event_type="keyword.deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="keywords",
        subject_id=keyword.id,
        payload={"remove_from_all_stories": True, "review_id": review_item.id},
    )
    return {
        "action": "deleted",
        "affected_story_count": len(affected_story_ids),
    }


def _require_dataset_match(review_item: ReviewItem, dataset_id: str) -> Dataset:
    if review_item.dataset_id != dataset_id:
        raise ReviewConflictError("Review item no longer matches the active dataset subject.")
    dataset = review_item.dataset
    if dataset is None:
        raise ReviewConflictError("Review item dataset is missing.")
    return dataset


def _metadata_position(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _coerce_story_trope_origin(value: str, *, fallback: StoryTropeOrigin) -> StoryTropeOrigin:
    try:
        return StoryTropeOrigin(value)
    except ValueError:
        return fallback


def _coerce_assignment_status(value: str, *, fallback: AssignmentStatus) -> AssignmentStatus:
    try:
        return AssignmentStatus(value)
    except ValueError:
        return fallback


def _touch_affected_stories(session: Session, story_ids: set[str]) -> set[str]:
    from app.services.stories import sync_story_derived_fields

    if not story_ids:
        return set()
    stories = session.scalars(
        select(Story)
        .where(Story.id.in_(story_ids))
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
        )
    ).all()
    affected_dataset_ids: set[str] = set()
    for story in stories:
        sync_story_derived_fields(story)
        story.version += 1
        affected_dataset_ids.add(story.dataset_id)
    return affected_dataset_ids


def _bump_dataset_versions(session: Session, active_dataset_id: str, affected_dataset_ids: set[str]) -> None:
    dataset_ids = set(affected_dataset_ids) or {active_dataset_id}
    for dataset in session.scalars(select(Dataset).where(Dataset.id.in_(dataset_ids))).all():
        dataset.version += 1


def _apply_trope_merge(
    session: Session,
    *,
    dataset_id: str,
    source_trope_id: str,
    target_trope_id: str,
) -> set[str]:
    source_links = list(
        session.scalars(
            select(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(
                Story.dataset_id == dataset_id,
                StoryTrope.trope_id == source_trope_id,
            )
            .options(selectinload(StoryTrope.story))
        ).all()
    )
    source_story_ids = [link.story_id for link in source_links]
    target_links_by_story = {}
    if source_story_ids:
        target_links_by_story = {
            link.story_id: link
            for link in session.scalars(
                select(StoryTrope)
                .join(Story, Story.id == StoryTrope.story_id)
                .where(
                    Story.dataset_id == dataset_id,
                    StoryTrope.trope_id == target_trope_id,
                    StoryTrope.story_id.in_(source_story_ids),
                )
            ).all()
        }

    affected_story_ids: set[str] = set()
    for source_link in source_links:
        affected_story_ids.add(source_link.story_id)
        target_link = target_links_by_story.get(source_link.story_id)
        if target_link is None:
            session.add(
                StoryTrope(
                    story_id=source_link.story_id,
                    trope_id=target_trope_id,
                    origin=StoryTropeOrigin.MERGE,
                    status=source_link.status,
                    position=source_link.position,
                )
            )
        else:
            _merge_story_trope_metadata(target_link, source_link)
        session.delete(source_link)

    session.flush()
    if session.scalar(select(func.count()).select_from(StoryTrope).where(StoryTrope.trope_id == source_trope_id)):
        raise ReviewConflictError("Source trope still has assignments and cannot be deleted.")

    source_trope = session.scalar(
        select(Trope).where(
            Trope.id == source_trope_id,
            Trope.dataset_id == dataset_id,
        )
    )
    if source_trope is not None:
        _delete_trope_artifacts(session, source_trope_id)
        session.delete(source_trope)
    return affected_story_ids


def _delete_trope_everywhere(session: Session, *, dataset_id: str, trope_id: str) -> set[str]:
    source_links = list(
        session.scalars(
            select(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(
                Story.dataset_id == dataset_id,
                StoryTrope.trope_id == trope_id,
            )
        ).all()
    )
    affected_story_ids = {link.story_id for link in source_links}
    for link in source_links:
        session.delete(link)
    session.flush()
    if session.scalar(select(func.count()).select_from(StoryTrope).where(StoryTrope.trope_id == trope_id)):
        raise ReviewConflictError("Trope still has assignments and cannot be deleted.")
    trope = session.scalar(
        select(Trope).where(
            Trope.id == trope_id,
            Trope.dataset_id == dataset_id,
        )
    )
    if trope is None:
        raise ReviewNotFoundError("Pending trope not found.")
    _delete_trope_artifacts(session, trope_id)
    session.delete(trope)
    return affected_story_ids


def _apply_keyword_merge(
    session: Session,
    *,
    dataset_id: str,
    source_keyword_id: str,
    target_keyword_id: str,
) -> set[str]:
    source_links = list(
        session.scalars(
            select(StoryKeyword)
            .join(Story, Story.id == StoryKeyword.story_id)
            .where(
                Story.dataset_id == dataset_id,
                StoryKeyword.keyword_id == source_keyword_id,
            )
            .options(selectinload(StoryKeyword.story))
        ).all()
    )
    source_story_ids = [link.story_id for link in source_links]
    target_links_by_story = {}
    if source_story_ids:
        target_links_by_story = {
            link.story_id: link
            for link in session.scalars(
                select(StoryKeyword)
                .join(Story, Story.id == StoryKeyword.story_id)
                .where(
                    Story.dataset_id == dataset_id,
                    StoryKeyword.keyword_id == target_keyword_id,
                    StoryKeyword.story_id.in_(source_story_ids),
                )
            ).all()
        }

    affected_story_ids: set[str] = set()
    for source_link in source_links:
        affected_story_ids.add(source_link.story_id)
        target_link = target_links_by_story.get(source_link.story_id)
        if target_link is None:
            session.add(
                StoryKeyword(
                    story_id=source_link.story_id,
                    keyword_id=target_keyword_id,
                    position=source_link.position,
                )
            )
        else:
            if source_link.position is not None and (
                target_link.position is None or source_link.position < target_link.position
            ):
                target_link.position = source_link.position
        session.delete(source_link)

    session.flush()
    if session.scalar(select(func.count()).select_from(StoryKeyword).where(StoryKeyword.keyword_id == source_keyword_id)):
        raise ReviewConflictError("Source keyword still has assignments and cannot be deleted.")

    source_keyword = session.scalar(
        select(Keyword).where(
            Keyword.id == source_keyword_id,
            Keyword.dataset_id == dataset_id,
        )
    )
    if source_keyword is not None:
        _delete_keyword_artifacts(session, source_keyword_id)
        session.delete(source_keyword)
    return affected_story_ids


def _delete_keyword_everywhere(session: Session, *, dataset_id: str, keyword_id: str) -> set[str]:
    source_links = list(
        session.scalars(
            select(StoryKeyword)
            .join(Story, Story.id == StoryKeyword.story_id)
            .where(
                Story.dataset_id == dataset_id,
                StoryKeyword.keyword_id == keyword_id,
            )
        ).all()
    )
    affected_story_ids = {link.story_id for link in source_links}
    for link in source_links:
        session.delete(link)
    session.flush()
    if session.scalar(select(func.count()).select_from(StoryKeyword).where(StoryKeyword.keyword_id == keyword_id)):
        raise ReviewConflictError("Keyword still has assignments and cannot be deleted.")
    keyword = session.scalar(
        select(Keyword).where(
            Keyword.id == keyword_id,
            Keyword.dataset_id == dataset_id,
        )
    )
    if keyword is None:
        raise ReviewNotFoundError("Pending keyword not found.")
    _delete_keyword_artifacts(session, keyword_id)
    session.delete(keyword)
    return affected_story_ids


def _merge_story_trope_metadata(target_link: StoryTrope, source_link: StoryTrope) -> None:
    if source_link.position is not None and (
        target_link.position is None or source_link.position < target_link.position
    ):
        target_link.position = source_link.position
    if source_link.status == AssignmentStatus.VALIDATED:
        target_link.status = AssignmentStatus.VALIDATED
    if _origin_priority(source_link.origin) > _origin_priority(target_link.origin):
        target_link.origin = source_link.origin


def _origin_priority(origin: StoryTropeOrigin) -> int:
    priorities = {
        StoryTropeOrigin.SEMANTIC_SUGGESTION: 1,
        StoryTropeOrigin.MERGE: 2,
        StoryTropeOrigin.CSV_IMPORT: 3,
        StoryTropeOrigin.HUMAN_ENTERED: 4,
        StoryTropeOrigin.HUMAN_APPROVED: 5,
    }
    return priorities.get(origin, 0)


def _refresh_trope_cached_story_count(session: Session, trope_id: str) -> None:
    trope = session.get(Trope, trope_id)
    if trope is None:
        return
    trope.cached_story_count = int(
        session.scalar(select(func.count(func.distinct(StoryTrope.story_id))).where(StoryTrope.trope_id == trope_id)) or 0
    )


def _refresh_keyword_cached_story_count(session: Session, keyword_id: str) -> None:
    keyword = session.get(Keyword, keyword_id)
    if keyword is None:
        return
    keyword.cached_story_count = int(
        session.scalar(select(func.count(func.distinct(StoryKeyword.story_id))).where(StoryKeyword.keyword_id == keyword_id))
        or 0
    )


def _delete_trope_artifacts(session: Session, trope_id: str) -> None:
    session.execute(
        delete(TermSimilarityCache).where(
            TermSimilarityCache.term_kind == TermKind.TROPE,
            or_(
                TermSimilarityCache.source_term_id == trope_id,
                TermSimilarityCache.target_term_id == trope_id,
            ),
        )
    )
    session.execute(delete(TermEmbedding).where(TermEmbedding.trope_id == trope_id))


def _delete_keyword_artifacts(session: Session, keyword_id: str) -> None:
    session.execute(
        delete(TermSimilarityCache).where(
            TermSimilarityCache.term_kind == TermKind.KEYWORD,
            or_(
                TermSimilarityCache.source_term_id == keyword_id,
                TermSimilarityCache.target_term_id == keyword_id,
            ),
        )
    )
    session.execute(delete(TermEmbedding).where(TermEmbedding.keyword_id == keyword_id))
