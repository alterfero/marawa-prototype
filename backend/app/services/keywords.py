from __future__ import annotations

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.parsing import clean_text, normalize_text
from app.db.models import (
    Dataset,
    DatasetStatus,
    Keyword,
    KeywordConfirmationStatus,
    Story,
    StoryKeyword,
    StoryTheme,
    StoryTrope,
    TermEmbedding,
    TermKind,
    TermReviewStatus,
    TermSimilarityCache,
    UserRole,
)
from app.services.audit import record_audit_event
from app.services.reviews import queue_term_review_item
from app.services.stories import sync_story_derived_fields


TITLE_FIELDS = [
    "Story title (Eng)",
    "Story title (French)",
    "Story title (other)",
]


class KeywordLookupError(ValueError):
    """Base error for keyword lookup operations."""


class KeywordLookupNotFoundError(KeywordLookupError):
    """Raised when a requested keyword does not exist."""


class KeywordMutationValidationError(KeywordLookupError):
    """Raised when a keyword mutation request is invalid."""


class KeywordVersionConflictError(KeywordLookupError):
    """Raised when a keyword mutation uses a stale version."""


class KeywordDeletionConflictError(KeywordLookupError):
    """Raised when a keyword cannot safely be deleted."""


def list_canonical_keywords(
    session: Session,
    *,
    unused_only: bool = False,
    query: str | None = None,
    limit: int = 100,
) -> list[dict]:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return []

    query_text = clean_text(query) if query is not None else ""
    statement = select(
        Keyword.id,
        Keyword.version,
        Keyword.text,
        Keyword.confirmation_status,
        Keyword.cached_story_count.label("story_count"),
    ).where(Keyword.dataset_id == active_dataset.id)
    if query_text:
        statement = statement.where(func.lower(Keyword.text).contains(query_text.lower()))
    if unused_only:
        statement = statement.where(Keyword.cached_story_count == 0)

    return [
        {
            "id": row.id,
            "version": row.version,
            "text": row.text,
            "confirmation_status": row.confirmation_status.value,
            "story_count": int(row.story_count or 0),
        }
        for row in session.execute(statement.order_by(Keyword.text.asc(), Keyword.id.asc()).limit(limit)).all()
    ]


def ensure_canonical_keyword(
    session: Session,
    text: str,
    *,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[dict, bool]:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise KeywordMutationValidationError("No active dataset is available.")

    keyword_text = clean_text(text)
    marker = normalize_text(keyword_text)
    if not marker:
        raise KeywordMutationValidationError("Keyword text cannot be empty.")

    keyword = session.scalar(
        select(Keyword).where(
            Keyword.dataset_id == active_dataset.id,
            Keyword.normalized_text == marker,
        )
    )
    if keyword is not None:
        return _serialize_keyword_summary(keyword), False

    keyword = Keyword(
        dataset_id=active_dataset.id,
        text=keyword_text,
        review_status=TermReviewStatus.PENDING_REVIEW if actor_role == UserRole.CONTRIBUTOR else TermReviewStatus.APPROVED,
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    session.add(keyword)
    try:
        session.flush()
        if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
            queue_term_review_item(
                session,
                dataset_id=active_dataset.id,
                term_kind=TermKind.KEYWORD,
                subject_id=keyword.id,
                actor_user_id=actor_user_id,
                text=keyword.text,
            )
        record_audit_event(
            session,
            event_type="keyword.created",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="keywords",
            subject_id=keyword.id,
            payload={
                "created": True,
                "review_status": keyword.review_status.value,
            },
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        keyword = session.scalar(
            select(Keyword).where(
                Keyword.dataset_id == active_dataset.id,
                Keyword.normalized_text == marker,
            )
        )
        if keyword is None:
            raise
        return _serialize_keyword_summary(keyword), False

    return _serialize_keyword_summary(keyword), True


def get_keyword_detail(session: Session, keyword_id: str) -> dict:
    active_dataset = _require_active_dataset(session)
    keyword = _get_active_keyword(session, active_dataset.id, keyword_id)
    stories = session.scalars(
        select(Story)
        .join(StoryKeyword, StoryKeyword.story_id == Story.id)
        .where(
            Story.dataset_id == active_dataset.id,
            StoryKeyword.keyword_id == keyword.id,
        )
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()

    return {
        **_serialize_keyword_summary(keyword),
        "stories": [
            {
                "id": story.id,
                "title": _story_title(story),
                "source_row_number": story.source_row_number,
            }
            for story in stories
        ],
    }


def set_keyword_confirmation_status(
    session: Session,
    keyword_id: str,
    *,
    expected_version: int,
    confirmation_status: KeywordConfirmationStatus,
    actor_user_id: str,
) -> dict:
    active_dataset = _require_active_dataset(session)
    keyword = _get_active_keyword(session, active_dataset.id, keyword_id)
    _assert_keyword_version(keyword, expected_version)

    if keyword.confirmation_status != confirmation_status:
        previous_status = keyword.confirmation_status.value
        keyword.confirmation_status = confirmation_status
        keyword.version += 1
        keyword.updated_by_user_id = actor_user_id
        record_audit_event(
            session,
            event_type="keyword.confirmation_status_updated",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="keywords",
            subject_id=keyword.id,
            payload={
                "previous_confirmation_status": previous_status,
                "confirmation_status": keyword.confirmation_status.value,
                "version": keyword.version,
            },
        )

    session.commit()
    return _serialize_keyword_summary(keyword)


def update_keyword_text(
    session: Session,
    keyword_id: str,
    *,
    expected_version: int,
    text: str,
    actor_user_id: str,
) -> dict:
    active_dataset = _require_active_dataset(session)
    keyword = _get_active_keyword(session, active_dataset.id, keyword_id)
    _assert_keyword_version(keyword, expected_version)

    keyword_text = clean_text(text)
    marker = normalize_text(keyword_text)
    if not marker:
        raise KeywordMutationValidationError("Keyword text cannot be empty.")

    existing_keyword = session.scalar(
        select(Keyword).where(
            Keyword.dataset_id == active_dataset.id,
            Keyword.normalized_text == marker,
            Keyword.id != keyword.id,
        )
    )
    if existing_keyword is not None:
        raise KeywordMutationValidationError("Another canonical keyword already uses that text.")

    if keyword.text == keyword_text:
        return _serialize_keyword_summary(keyword)

    previous_text = keyword.text
    keyword.text = keyword_text
    keyword.version += 1
    keyword.updated_by_user_id = actor_user_id

    affected_stories = session.scalars(
        select(Story)
        .join(StoryKeyword, StoryKeyword.story_id == Story.id)
        .where(StoryKeyword.keyword_id == keyword.id)
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
            selectinload(Story.theme_links).selectinload(StoryTheme.theme),
        )
    ).all()
    for story in affected_stories:
        sync_story_derived_fields(story)
        story.version += 1

    _delete_keyword_artifacts(session, keyword.id)
    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="keyword.text_updated",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="keywords",
        subject_id=keyword.id,
        payload={
            "previous_text": previous_text,
            "text": keyword.text,
            "affected_story_count": len(affected_stories),
            "rebuild_queued": False,
            "version": keyword.version,
        },
    )
    session.commit()
    return _serialize_keyword_summary(keyword)


def set_keyword_confirmation_statuses(
    session: Session,
    *,
    updates: list[dict[str, object]],
    actor_user_id: str,
) -> list[dict]:
    if not updates:
        raise KeywordMutationValidationError("Provide at least one keyword confirmation update.")

    keyword_ids = [clean_text(str(update.get("keyword_id", ""))) for update in updates]
    if any(not keyword_id for keyword_id in keyword_ids):
        raise KeywordMutationValidationError("Every keyword confirmation update must include a keyword_id.")
    if len(set(keyword_ids)) != len(keyword_ids):
        raise KeywordMutationValidationError("Duplicate keyword confirmation updates are not allowed.")

    active_dataset = _require_active_dataset(session)
    keywords_by_id = {
        keyword.id: keyword
        for keyword in session.scalars(
            select(Keyword).where(
                Keyword.dataset_id == active_dataset.id,
                Keyword.id.in_(keyword_ids),
            )
        ).all()
    }
    if len(keywords_by_id) != len(keyword_ids):
        raise KeywordLookupNotFoundError("Canonical keyword not found.")

    for update, keyword_id in zip(updates, keyword_ids, strict=True):
        expected_version = int(update.get("expected_version", 0))
        confirmation_status = update.get("confirmation_status")
        if not isinstance(confirmation_status, KeywordConfirmationStatus):
            raise KeywordMutationValidationError("Invalid keyword confirmation status.")
        keyword = keywords_by_id[keyword_id]
        _assert_keyword_version(keyword, expected_version)
        if keyword.confirmation_status == confirmation_status:
            continue
        previous_status = keyword.confirmation_status.value
        keyword.confirmation_status = confirmation_status
        keyword.version += 1
        keyword.updated_by_user_id = actor_user_id
        record_audit_event(
            session,
            event_type="keyword.confirmation_status_updated",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="keywords",
            subject_id=keyword.id,
            payload={
                "previous_confirmation_status": previous_status,
                "confirmation_status": keyword.confirmation_status.value,
                "version": keyword.version,
            },
        )

    session.flush()
    session.commit()
    return [_serialize_keyword_summary(keywords_by_id[keyword_id]) for keyword_id in keyword_ids]


def merge_unconfirmed_keyword(
    session: Session,
    source_keyword_id: str,
    *,
    target_keyword_id: str,
    expected_source_version: int,
    actor_user_id: str,
) -> tuple[Dataset, dict]:
    """Merge an unconfirmed keyword into a canonical keyword in the active dataset."""
    active_dataset = _require_active_dataset(session)
    source_keyword = _get_active_keyword(session, active_dataset.id, source_keyword_id)
    target_keyword = _get_active_keyword(session, active_dataset.id, target_keyword_id)
    _assert_keyword_version(source_keyword, expected_source_version)

    if source_keyword.id == target_keyword.id:
        raise KeywordMutationValidationError("A keyword cannot be merged with itself.")
    if source_keyword.confirmation_status != KeywordConfirmationStatus.UNCONFIRMED:
        raise KeywordMutationValidationError("Only unconfirmed keywords can be merged.")
    if target_keyword.confirmation_status != KeywordConfirmationStatus.CANONICAL:
        raise KeywordMutationValidationError("A keyword can only be merged into a canonical keyword.")

    source_links = list(session.scalars(select(StoryKeyword).where(StoryKeyword.keyword_id == source_keyword.id)).all())
    affected_story_ids = {link.story_id for link in source_links}
    target_story_ids = set(
        session.scalars(select(StoryKeyword.story_id).where(StoryKeyword.keyword_id == target_keyword.id)).all()
    )

    for link in source_links:
        if link.story_id in target_story_ids:
            session.delete(link)
        else:
            link.keyword_id = target_keyword.id
    session.flush()

    affected_stories = session.scalars(
        select(Story)
        .where(Story.id.in_(affected_story_ids))
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
            selectinload(Story.theme_links).selectinload(StoryTheme.theme),
        )
    ).all()
    for story in affected_stories:
        sync_story_derived_fields(story)
        story.version += 1

    target_keyword.cached_story_count = int(
        session.scalar(
            select(func.count()).select_from(StoryKeyword).where(StoryKeyword.keyword_id == target_keyword.id)
        )
        or 0
    )
    target_keyword.version += 1
    target_keyword.updated_by_user_id = actor_user_id
    _delete_keyword_artifacts(session, source_keyword.id)
    session.delete(source_keyword)
    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="keyword.merged",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="keywords",
        subject_id=source_keyword.id,
        payload={
            "source_keyword_id": source_keyword.id,
            "target_keyword_id": target_keyword.id,
            "affected_story_count": len(affected_stories),
            "target_keyword_version": target_keyword.version,
            "rebuild_queued": False,
        },
    )
    session.commit()
    return active_dataset, {
        "source_keyword_id": source_keyword_id,
        "target_keyword": _serialize_keyword_summary(target_keyword),
        "affected_story_count": len(affected_stories),
    }


def delete_keyword(
    session: Session,
    keyword_id: str,
    *,
    expected_version: int,
    remove_from_all_stories: bool,
    actor_user_id: str,
) -> tuple[Dataset, dict]:
    active_dataset = _require_active_dataset(session)
    keyword = _get_active_keyword(session, active_dataset.id, keyword_id)
    _assert_keyword_version(keyword, expected_version)

    links = session.scalars(
        select(StoryKeyword)
        .where(StoryKeyword.keyword_id == keyword.id)
        .options(selectinload(StoryKeyword.story))
    ).all()
    if links and not remove_from_all_stories:
        raise KeywordDeletionConflictError(
            "Keyword still has story assignments. Set remove_from_all_stories=true to delete it everywhere."
        )

    affected_story_ids = {link.story_id for link in links}
    for link in links:
        session.delete(link)
    session.flush()

    affected_stories = session.scalars(
        select(Story)
        .where(Story.id.in_(affected_story_ids))
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
            selectinload(Story.theme_links).selectinload(StoryTheme.theme),
        )
    ).all()
    for story in affected_stories:
        sync_story_derived_fields(story)
        story.version += 1

    _delete_keyword_artifacts(session, keyword.id)
    session.delete(keyword)
    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="keyword.deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="keywords",
        subject_id=keyword.id,
        payload={
            "affected_story_count": len(affected_stories),
            "remove_from_all_stories": remove_from_all_stories,
            "rebuild_queued": False,
        },
    )
    session.commit()
    return active_dataset, {
        "deleted_keyword_id": keyword_id,
        "affected_story_count": len(affected_stories),
    }


def _get_active_dataset(session: Session) -> Dataset | None:
    return session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))


def _require_active_dataset(session: Session) -> Dataset:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise KeywordLookupNotFoundError("Canonical keyword not found.")
    return active_dataset


def _get_active_keyword(session: Session, dataset_id: str, keyword_id: str) -> Keyword:
    keyword = session.scalar(
        select(Keyword).where(
            Keyword.id == keyword_id,
            Keyword.dataset_id == dataset_id,
        )
    )
    if keyword is None:
        raise KeywordLookupNotFoundError("Canonical keyword not found.")
    return keyword


def _assert_keyword_version(keyword: Keyword, expected_version: int) -> None:
    if expected_version < 1:
        raise KeywordMutationValidationError("Expected keyword version must be at least 1.")
    if keyword.version != expected_version:
        raise KeywordVersionConflictError(
            f"Keyword version conflict: expected version {expected_version}, current version is {keyword.version}."
        )


def _story_title(story: Story) -> str:
    fields = story.fields_json or {}
    for field_name in TITLE_FIELDS:
        value = clean_text(fields.get(field_name, ""))
        if value:
            return value
    return story.id


def _serialize_keyword_summary(keyword: Keyword) -> dict:
    return {
        "id": keyword.id,
        "version": keyword.version,
        "text": keyword.text,
        "confirmation_status": keyword.confirmation_status.value,
        "story_count": int(keyword.cached_story_count or 0),
    }


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
