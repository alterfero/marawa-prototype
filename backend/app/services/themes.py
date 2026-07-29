from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.parsing import clean_text, normalize_text
from app.db.models import (
    Dataset,
    DatasetStatus,
    Story,
    StoryKeyword,
    StoryTheme,
    StoryTrope,
    Theme,
    ThemeConfirmationStatus,
)
from app.services.audit import record_audit_event
from app.services.stories import sync_story_derived_fields


TITLE_FIELDS = [
    "Story title (Eng)",
    "Story title (French)",
    "Story title (other)",
]


class ThemeLookupError(ValueError):
    """Base error for theme lookup operations."""


class ThemeLookupNotFoundError(ThemeLookupError):
    """Raised when a requested theme does not exist."""


class ThemeMutationValidationError(ThemeLookupError):
    """Raised when a theme mutation request is invalid."""


class ThemeVersionConflictError(ThemeLookupError):
    """Raised when a theme mutation uses a stale version."""


class ThemeDeletionConflictError(ThemeLookupError):
    """Raised when a theme cannot safely be deleted."""


def list_canonical_themes(
    session: Session,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict]:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return []

    query_text = clean_text(query) if query is not None else ""
    statement = select(
        Theme.id,
        Theme.version,
        Theme.text,
        Theme.confirmation_status,
        Theme.cached_story_count.label("story_count"),
    ).where(Theme.dataset_id == active_dataset.id)
    if query_text:
        statement = statement.where(func.lower(Theme.text).contains(query_text.lower()))

    return [
        {
            "id": row.id,
            "version": row.version,
            "text": row.text,
            "confirmation_status": row.confirmation_status.value,
            "story_count": int(row.story_count or 0),
        }
        for row in session.execute(statement.order_by(Theme.text.asc(), Theme.id.asc()).limit(limit)).all()
    ]


def get_theme_detail(session: Session, theme_id: str) -> dict:
    active_dataset = _require_active_dataset(session)
    theme = _get_active_theme(session, active_dataset.id, theme_id)
    stories = session.scalars(
        select(Story)
        .join(StoryTheme, StoryTheme.story_id == Story.id)
        .where(
            Story.dataset_id == active_dataset.id,
            StoryTheme.theme_id == theme.id,
        )
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()

    return {
        **_serialize_theme_summary(theme),
        "stories": [
            {
                "id": story.id,
                "title": _story_title(story),
                "source_row_number": story.source_row_number,
            }
            for story in stories
        ],
    }


def set_theme_confirmation_status(
    session: Session,
    theme_id: str,
    *,
    expected_version: int,
    confirmation_status: ThemeConfirmationStatus,
    actor_user_id: str,
) -> dict:
    active_dataset = _require_active_dataset(session)
    theme = _get_active_theme(session, active_dataset.id, theme_id)
    _assert_theme_version(theme, expected_version)

    if theme.confirmation_status != confirmation_status:
        previous_status = theme.confirmation_status.value
        theme.confirmation_status = confirmation_status
        theme.version += 1
        theme.updated_by_user_id = actor_user_id
        record_audit_event(
            session,
            event_type="theme.confirmation_status_updated",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="themes",
            subject_id=theme.id,
            payload={
                "previous_confirmation_status": previous_status,
                "confirmation_status": theme.confirmation_status.value,
                "version": theme.version,
            },
        )

    session.commit()
    return _serialize_theme_summary(theme)


def update_theme_text(
    session: Session,
    theme_id: str,
    *,
    expected_version: int,
    text: str,
    actor_user_id: str,
) -> dict:
    active_dataset = _require_active_dataset(session)
    theme = _get_active_theme(session, active_dataset.id, theme_id)
    _assert_theme_version(theme, expected_version)

    theme_text = clean_text(text)
    marker = normalize_text(theme_text)
    if not marker:
        raise ThemeMutationValidationError("Theme text cannot be empty.")

    existing_theme = session.scalar(
        select(Theme).where(
            Theme.dataset_id == active_dataset.id,
            Theme.normalized_text == marker,
            Theme.id != theme.id,
        )
    )
    if existing_theme is not None:
        raise ThemeMutationValidationError("Another canonical theme already uses that text.")

    if theme.text == theme_text:
        return _serialize_theme_summary(theme)

    previous_text = theme.text
    theme.text = theme_text
    theme.version += 1
    theme.updated_by_user_id = actor_user_id

    affected_stories = session.scalars(
        select(Story)
        .join(StoryTheme, StoryTheme.story_id == Story.id)
        .where(StoryTheme.theme_id == theme.id)
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
            selectinload(Story.theme_links).selectinload(StoryTheme.theme),
        )
    ).all()
    for story in affected_stories:
        sync_story_derived_fields(story)
        story.version += 1

    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="theme.text_updated",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="themes",
        subject_id=theme.id,
        payload={
            "previous_text": previous_text,
            "text": theme.text,
            "affected_story_count": len(affected_stories),
            "version": theme.version,
        },
    )
    session.commit()
    return _serialize_theme_summary(theme)


def merge_unconfirmed_theme(
    session: Session,
    source_theme_id: str,
    *,
    target_theme_id: str,
    expected_source_version: int,
    actor_user_id: str,
) -> tuple[Dataset, dict]:
    """Merge an unconfirmed theme into a canonical theme in the active dataset."""
    active_dataset = _require_active_dataset(session)
    source_theme = _get_active_theme(session, active_dataset.id, source_theme_id)
    target_theme = _get_active_theme(session, active_dataset.id, target_theme_id)
    _assert_theme_version(source_theme, expected_source_version)

    if source_theme.id == target_theme.id:
        raise ThemeMutationValidationError("A theme cannot be merged with itself.")
    if source_theme.confirmation_status != ThemeConfirmationStatus.UNCONFIRMED:
        raise ThemeMutationValidationError("Only unconfirmed themes can be merged.")
    if target_theme.confirmation_status != ThemeConfirmationStatus.CANONICAL:
        raise ThemeMutationValidationError("A theme can only be merged into a canonical theme.")

    source_links = list(
        session.scalars(select(StoryTheme).where(StoryTheme.theme_id == source_theme.id)).all()
    )
    affected_story_ids = {link.story_id for link in source_links}
    target_story_ids = set(
        session.scalars(
            select(StoryTheme.story_id).where(StoryTheme.theme_id == target_theme.id)
        ).all()
    )

    for link in source_links:
        if link.story_id in target_story_ids:
            session.delete(link)
        else:
            link.theme_id = target_theme.id
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

    target_theme.cached_story_count = int(
        session.scalar(
            select(func.count()).select_from(StoryTheme).where(StoryTheme.theme_id == target_theme.id)
        )
        or 0
    )
    target_theme.version += 1
    target_theme.updated_by_user_id = actor_user_id
    session.delete(source_theme)
    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="theme.merged",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="themes",
        subject_id=source_theme.id,
        payload={
            "source_theme_id": source_theme.id,
            "target_theme_id": target_theme.id,
            "affected_story_count": len(affected_stories),
            "target_theme_version": target_theme.version,
        },
    )
    session.commit()
    return active_dataset, {
        "source_theme_id": source_theme_id,
        "target_theme": _serialize_theme_summary(target_theme),
        "affected_story_count": len(affected_stories),
    }


def delete_theme(
    session: Session,
    theme_id: str,
    *,
    expected_version: int,
    remove_from_all_stories: bool,
    actor_user_id: str,
) -> tuple[Dataset, dict]:
    active_dataset = _require_active_dataset(session)
    theme = _get_active_theme(session, active_dataset.id, theme_id)
    _assert_theme_version(theme, expected_version)

    links = session.scalars(
        select(StoryTheme)
        .where(StoryTheme.theme_id == theme.id)
        .options(selectinload(StoryTheme.story))
    ).all()
    if links and not remove_from_all_stories:
        raise ThemeDeletionConflictError(
            "Theme still has story assignments. Set remove_from_all_stories=true to delete it everywhere."
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

    session.delete(theme)
    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="theme.deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="themes",
        subject_id=theme.id,
        payload={
            "affected_story_count": len(affected_stories),
            "remove_from_all_stories": remove_from_all_stories,
        },
    )
    session.commit()
    return active_dataset, {
        "deleted_theme_id": theme_id,
        "affected_story_count": len(affected_stories),
    }


def _get_active_dataset(session: Session) -> Dataset | None:
    return session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))


def _require_active_dataset(session: Session) -> Dataset:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise ThemeLookupNotFoundError("Canonical theme not found.")
    return active_dataset


def _get_active_theme(session: Session, dataset_id: str, theme_id: str) -> Theme:
    theme = session.scalar(
        select(Theme).where(
            Theme.id == theme_id,
            Theme.dataset_id == dataset_id,
        )
    )
    if theme is None:
        raise ThemeLookupNotFoundError("Canonical theme not found.")
    return theme


def _assert_theme_version(theme: Theme, expected_version: int) -> None:
    if expected_version < 1:
        raise ThemeMutationValidationError("Expected theme version must be at least 1.")
    if theme.version != expected_version:
        raise ThemeVersionConflictError(
            f"Theme version conflict: expected version {expected_version}, current version is {theme.version}."
        )


def _story_title(story: Story) -> str:
    fields = story.fields_json or {}
    for field_name in TITLE_FIELDS:
        value = clean_text(fields.get(field_name, ""))
        if value:
            return value
    return story.id


def _serialize_theme_summary(theme: Theme) -> dict:
    return {
        "id": theme.id,
        "version": theme.version,
        "text": theme.text,
        "confirmation_status": theme.confirmation_status.value,
        "story_count": int(theme.cached_story_count or 0),
    }
