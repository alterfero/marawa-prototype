from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.parsing import clean_text, normalize_text
from app.db.models import Dataset, DatasetStatus, Keyword, Story, StoryKeyword, TermKind, TermReviewStatus, UserRole
from app.services.audit import record_audit_event
from app.services.reviews import queue_term_review_item


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
    """Raised when a keyword create request is invalid."""


def list_canonical_keywords(
    session: Session,
    *,
    unused_only: bool = False,
    query: str | None = None,
    limit: int = 100,
) -> list[dict]:
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        return []

    query_text = clean_text(query) if query is not None else ""
    statement = (
        select(
            Keyword.id,
            Keyword.text,
            Keyword.cached_story_count.label("story_count"),
        )
        .select_from(Keyword)
        .where(Keyword.dataset_id == active_dataset.id)
    )
    if query_text:
        statement = statement.where(func.lower(Keyword.text).contains(query_text.lower()))
    if unused_only:
        statement = statement.where(Keyword.cached_story_count == 0)

    rows = session.execute(
        statement.order_by(Keyword.text.asc(), Keyword.id.asc()).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "text": row.text,
            "story_count": int(row.story_count or 0),
        }
        for row in rows
    ]


def ensure_canonical_keyword(
    session: Session,
    text: str,
    *,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[dict, bool]:
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
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
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        raise KeywordLookupNotFoundError("Canonical keyword not found.")

    keyword = session.scalar(
        select(Keyword).where(
            Keyword.id == keyword_id,
            Keyword.dataset_id == active_dataset.id,
        )
    )
    if keyword is None:
        raise KeywordLookupNotFoundError("Canonical keyword not found.")

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
        "id": keyword.id,
        "text": keyword.text,
        "story_count": len(stories),
        "stories": [
            {
                "id": story.id,
                "title": _story_title(story),
                "source_row_number": story.source_row_number,
            }
            for story in stories
        ],
    }


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
        "text": keyword.text,
        "story_count": int(keyword.cached_story_count or 0),
    }
