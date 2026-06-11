from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.parsing import clean_text, normalize_text
from app.db.models import Dataset, DatasetStatus, Story, StoryTrope, Trope


TITLE_FIELDS = [
    "Story title (Eng)",
    "Story title (French)",
    "Story title (other)",
]


class TropeLookupError(ValueError):
    """Base error for trope lookup operations."""


class TropeLookupNotFoundError(TropeLookupError):
    """Raised when a requested trope does not exist."""


class TropeMutationValidationError(TropeLookupError):
    """Raised when a trope create request is invalid."""


def ensure_canonical_trope(session: Session, text: str) -> tuple[dict, bool]:
    trope_text = clean_text(text)
    marker = normalize_text(trope_text)
    if not marker:
        raise TropeMutationValidationError("Trope text cannot be empty.")

    trope = session.scalar(select(Trope).where(Trope.normalized_text == marker))
    if trope is not None:
        return _serialize_trope_summary(trope), False

    trope = Trope(text=trope_text)
    session.add(trope)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        trope = session.scalar(select(Trope).where(Trope.normalized_text == marker))
        if trope is None:
            raise
        return _serialize_trope_summary(trope), False

    return _serialize_trope_summary(trope), True


def get_trope_detail(session: Session, trope_id: str) -> dict:
    trope = session.get(Trope, trope_id)
    if trope is None:
        raise TropeLookupNotFoundError("Canonical trope not found.")

    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        return {
            "id": trope.id,
            "text": trope.text,
            "story_count": 0,
            "stories": [],
        }

    stories = session.scalars(
        select(Story)
        .join(StoryTrope, StoryTrope.story_id == Story.id)
        .where(
            Story.dataset_id == active_dataset.id,
            StoryTrope.trope_id == trope.id,
        )
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()

    return {
        "id": trope.id,
        "text": trope.text,
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


def _serialize_trope_summary(trope: Trope) -> dict:
    return {
        "id": trope.id,
        "text": trope.text,
        "story_count": int(trope.cached_story_count or 0),
    }
