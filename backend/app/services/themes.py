from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.parsing import clean_text, normalize_text
from app.db.models import (
    Dataset,
    DatasetStatus,
    Story,
    StoryKeyword,
    StoryTheme,
    StoryTrope,
    TermEmbedding,
    TermKind,
    TermSimilarityCache,
    Theme,
    ThemeConfirmationStatus,
)
from app.services.audit import record_audit_event
from app.services.stories import sync_story_derived_fields

if TYPE_CHECKING:
    from app.services.search_service import SearchService


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


class ThemeCurationConflictError(ThemeLookupError):
    """Raised when a theme curation action is blocked by current data state."""


class ThemeCurationValidationError(ThemeLookupError):
    """Raised when a theme curation request is invalid."""


def list_canonical_themes(
    session: Session,
    *,
    unused_only: bool = False,
    query: str | None = None,
    limit: int = 100,
    include_story_ids: bool = False,
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
    if unused_only:
        statement = statement.where(Theme.cached_story_count == 0)

    rows = session.execute(statement.order_by(Theme.text.asc(), Theme.id.asc()).limit(limit)).all()
    story_ids_by_theme_id: dict[str, list[str]] = {}
    if include_story_ids and rows:
        theme_ids = [row.id for row in rows]
        story_ids_by_theme_id = {theme_id: [] for theme_id in theme_ids}
        story_rows = session.execute(
            select(StoryTheme.theme_id.label("theme_id"), Story.id.label("story_id"))
            .select_from(StoryTheme)
            .join(Story, Story.id == StoryTheme.story_id)
            .where(
                Story.dataset_id == active_dataset.id,
                StoryTheme.theme_id.in_(theme_ids),
            )
            .order_by(
                StoryTheme.theme_id.asc(),
                case((Story.source_row_number.is_(None), 1), else_=0),
                Story.source_row_number.asc(),
                Story.created_at.asc(),
                Story.id.asc(),
            )
        ).all()
        for story_row in story_rows:
            story_ids_by_theme_id.setdefault(story_row.theme_id, []).append(story_row.story_id)

    return [
        {
            "id": row.id,
            "version": row.version,
            "text": row.text,
            "confirmation_status": row.confirmation_status.value,
            "story_count": int(row.story_count or 0),
            "story_ids": story_ids_by_theme_id.get(row.id, []),
        }
        for row in rows
    ]


def ensure_canonical_theme(
    session: Session,
    text: str,
    *,
    actor_user_id: str | None = None,
) -> tuple[dict, bool]:
    active_dataset = _require_active_dataset(session)
    theme_text = clean_text(text)
    marker = normalize_text(theme_text)
    if not marker:
        raise ThemeMutationValidationError("Theme text cannot be empty.")

    theme = session.scalar(
        select(Theme).where(
            Theme.dataset_id == active_dataset.id,
            Theme.normalized_text == marker,
        )
    )
    if theme is not None:
        return _serialize_theme_summary(theme), False

    theme = Theme(
        dataset_id=active_dataset.id,
        text=theme_text,
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    session.add(theme)
    session.flush()
    record_audit_event(
        session,
        event_type="theme.created",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="themes",
        subject_id=theme.id,
        payload={"created": True},
    )
    session.commit()
    return _serialize_theme_summary(theme), True


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

    _delete_theme_artifacts(session, theme.id)
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


def set_theme_confirmation_statuses(
    session: Session,
    *,
    updates: list[dict[str, object]],
    actor_user_id: str,
) -> list[dict]:
    if not updates:
        raise ThemeMutationValidationError("Provide at least one theme confirmation update.")

    active_dataset = _require_active_dataset(session)
    theme_ids = [clean_text(str(update.get("theme_id", ""))) for update in updates]
    if any(not theme_id for theme_id in theme_ids):
        raise ThemeMutationValidationError("Every theme confirmation update must include a theme_id.")
    if len(set(theme_ids)) != len(theme_ids):
        raise ThemeMutationValidationError("Duplicate theme confirmation updates are not allowed.")

    themes_by_id = {
        theme.id: theme
        for theme in session.scalars(
            select(Theme).where(
                Theme.dataset_id == active_dataset.id,
                Theme.id.in_(theme_ids),
            )
        ).all()
    }
    if len(themes_by_id) != len(theme_ids):
        raise ThemeLookupNotFoundError("Canonical theme not found.")

    for update, theme_id in zip(updates, theme_ids, strict=True):
        confirmation_status = update.get("confirmation_status")
        if not isinstance(confirmation_status, ThemeConfirmationStatus):
            raise ThemeMutationValidationError("Invalid theme confirmation status.")
        theme = themes_by_id[theme_id]
        _assert_theme_version(theme, int(update.get("expected_version", 0)))
        if theme.confirmation_status == confirmation_status:
            continue
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

    session.flush()
    session.commit()
    return [_serialize_theme_summary(themes_by_id[theme_id]) for theme_id in theme_ids]


def merge_unconfirmed_theme(
    session: Session,
    source_theme_id: str,
    *,
    target_theme_id: str,
    expected_source_version: int,
    actor_user_id: str,
) -> tuple[Dataset, dict]:
    """Merge an unconfirmed theme into another active theme in the active dataset."""
    active_dataset = _require_active_dataset(session)
    source_theme = _get_active_theme(session, active_dataset.id, source_theme_id)
    target_theme = _get_active_theme(session, active_dataset.id, target_theme_id)
    _assert_theme_version(source_theme, expected_source_version)

    if source_theme.id == target_theme.id:
        raise ThemeMutationValidationError("A theme cannot be merged with itself.")
    if source_theme.confirmation_status != ThemeConfirmationStatus.UNCONFIRMED:
        raise ThemeMutationValidationError("Only unconfirmed themes can be merged.")
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
    _delete_theme_artifacts(session, source_theme.id)
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

    _delete_theme_artifacts(session, theme.id)
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


def list_near_duplicate_themes(session: Session, *, model_name: str) -> dict:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return _empty_near_duplicate_result(model_name)

    active_theme_ids = set(
        session.scalars(
            select(StoryTheme.theme_id).join(Story).where(Story.dataset_id == active_dataset.id)
        ).all()
    )
    if len(active_theme_ids) < 2:
        return _empty_near_duplicate_result(model_name)

    artifact_version = session.scalar(
        select(func.max(TermSimilarityCache.artifact_version)).where(
            TermSimilarityCache.term_kind == TermKind.THEME,
            TermSimilarityCache.model_name == model_name,
            or_(
                TermSimilarityCache.source_term_id.in_(active_theme_ids),
                TermSimilarityCache.target_term_id.in_(active_theme_ids),
            ),
        )
    )
    if artifact_version is None:
        return _empty_near_duplicate_result(model_name)

    entries = list(
        session.scalars(
            select(TermSimilarityCache).where(
                TermSimilarityCache.term_kind == TermKind.THEME,
                TermSimilarityCache.model_name == model_name,
                TermSimilarityCache.artifact_version == artifact_version,
            )
        ).all()
    )
    if not entries:
        return {
            **_empty_near_duplicate_result(model_name),
            "artifact_version": int(artifact_version),
        }

    theme_ids = {
        term_id
        for entry in entries
        for term_id in (entry.source_term_id, entry.target_term_id)
        if term_id in active_theme_ids
    }
    themes_by_id = {
        theme.id: theme
        for theme in session.scalars(
            select(Theme).where(Theme.dataset_id == active_dataset.id, Theme.id.in_(theme_ids))
        ).all()
    }

    pair_map: dict[tuple[str, str], dict] = {}
    for entry in entries:
        source_theme = themes_by_id.get(entry.source_term_id)
        target_theme = themes_by_id.get(entry.target_term_id)
        if source_theme is None or target_theme is None:
            continue
        stable_themes = sorted([source_theme, target_theme], key=lambda theme: (theme.text.lower(), theme.id))
        display_themes = stable_themes
        if (
            stable_themes[0].confirmation_status == ThemeConfirmationStatus.CANONICAL
            and stable_themes[1].confirmation_status == ThemeConfirmationStatus.UNCONFIRMED
        ):
            display_themes = [stable_themes[1], stable_themes[0]]
        pair_key = (stable_themes[0].id, stable_themes[1].id)
        candidate = {
            "source_theme": _serialize_theme_summary(display_themes[0]),
            "target_theme": _serialize_theme_summary(display_themes[1]),
            "similarity_score": float(entry.similarity_score),
            "metadata": entry.metadata_json or {},
        }
        existing = pair_map.get(pair_key)
        if existing is None or candidate["similarity_score"] > existing["similarity_score"]:
            pair_map[pair_key] = candidate

    items = sorted(
        [
            item
            for item in pair_map.values()
            if not (
                item["source_theme"]["confirmation_status"] == ThemeConfirmationStatus.CANONICAL.value
                and item["target_theme"]["confirmation_status"] == ThemeConfirmationStatus.CANONICAL.value
            )
        ],
        key=lambda item: (
            -item["similarity_score"],
            item["source_theme"]["text"].lower(),
            item["target_theme"]["text"].lower(),
        ),
    )
    return {
        "items": items,
        "artifact_version": int(artifact_version),
        "model_name": model_name,
        "total": len(items),
    }


def list_similar_unconfirmed_themes(
    session: Session,
    *,
    source_theme_id: str,
    minimum_similarity: float,
    search_service: SearchService,
    include_canonical: bool = False,
) -> dict:
    if not 0.0 <= minimum_similarity <= 1.0:
        raise ThemeCurationValidationError("Minimum similarity must be between 0 and 1.")

    active_dataset = _require_active_dataset(session)
    source_theme = _get_active_theme(session, active_dataset.id, source_theme_id)
    candidates = select(Theme).where(
        Theme.dataset_id == active_dataset.id,
        Theme.id != source_theme.id,
    )
    if not include_canonical:
        candidates = candidates.where(Theme.confirmation_status == ThemeConfirmationStatus.UNCONFIRMED)
    candidate_themes = list(session.scalars(candidates.order_by(Theme.text.asc(), Theme.id.asc())).all())
    artifact_version, scores_by_theme_id = search_service.get_similar_theme_scores(
        session,
        source_theme.id,
        [theme.id for theme in candidate_themes],
        minimum_score=minimum_similarity,
    )
    items = sorted(
        [
            {
                **_serialize_theme_summary(theme),
                "similarity_score": scores_by_theme_id[theme.id],
            }
            for theme in candidate_themes
            if theme.id in scores_by_theme_id
        ],
        key=lambda item: (-item["similarity_score"], item["text"].lower(), item["id"]),
    )
    return {
        "source_theme_id": source_theme.id,
        "items": items,
        "artifact_version": artifact_version,
        "model_name": search_service.model_name,
        "minimum_similarity": minimum_similarity,
        "total": len(items),
    }


def merge_themes(
    session: Session,
    *,
    source_theme_id: str,
    target_theme_id: str,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict]:
    dataset, summary = validate_theme_merges(
        session,
        merges=[{"source_theme_id": source_theme_id, "target_theme_id": target_theme_id}],
        actor_user_id=actor_user_id,
    )
    return dataset, summary["applied_merges"][0]


def validate_theme_merges(
    session: Session,
    *,
    merges: list[dict[str, str]],
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict]:
    active_dataset = _require_active_dataset(session)
    normalized_merges = _normalize_theme_merges(session, active_dataset.id, merges)
    affected_story_ids: set[str] = set()
    target_theme_ids: set[str] = set()
    summaries: list[dict[str, int | str]] = []

    for merge in normalized_merges:
        affected_ids = _apply_theme_merge(
            session,
            dataset_id=active_dataset.id,
            source_theme_id=merge["source_theme_id"],
            target_theme_id=merge["target_theme_id"],
        )
        affected_story_ids.update(affected_ids)
        target_theme_ids.add(merge["target_theme_id"])
        summaries.append(
            {
                "source_theme_id": merge["source_theme_id"],
                "target_theme_id": merge["target_theme_id"],
                "affected_story_count": len(affected_ids),
            }
        )

    session.flush()
    _touch_theme_stories(session, affected_story_ids)
    for target_theme_id in target_theme_ids:
        _refresh_theme_cached_story_count(session, target_theme_id)
    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="theme.merged" if len(summaries) == 1 else "theme.batch_merged",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="themes",
        subject_id=summaries[0]["source_theme_id"] if len(summaries) == 1 else None,
        payload={
            "merge_count": len(summaries),
            "affected_story_count": len(affected_story_ids),
            "merges": summaries,
            "rebuild_queued": False,
        },
    )
    session.commit()
    return session.get(Dataset, active_dataset.id), {
        "applied_merges": summaries,
        "merge_count": len(summaries),
        "affected_story_count": len(affected_story_ids),
    }


def delete_unused_themes(
    session: Session,
    *,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict]:
    active_dataset = _require_active_dataset(session)
    unused_themes = list(
        session.scalars(
            select(Theme).where(
                Theme.dataset_id == active_dataset.id,
                Theme.cached_story_count == 0,
                ~Theme.story_links.any(),
            )
        ).all()
    )
    if not unused_themes:
        return active_dataset, {"deleted_theme_count": 0}
    for theme in unused_themes:
        _delete_theme_artifacts(session, theme.id)
        session.delete(theme)
    active_dataset.version += 1
    record_audit_event(
        session,
        event_type="theme.unused_batch_deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="themes",
        payload={"deleted_theme_count": len(unused_themes), "rebuild_queued": False},
    )
    session.commit()
    return session.get(Dataset, active_dataset.id), {"deleted_theme_count": len(unused_themes)}


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


def _empty_near_duplicate_result(model_name: str) -> dict:
    return {
        "items": [],
        "artifact_version": None,
        "model_name": model_name,
        "total": 0,
    }


def _normalize_theme_merges(
    session: Session,
    dataset_id: str,
    merges: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not merges:
        raise ThemeCurationValidationError("At least one merge decision is required.")

    theme_ids = {
        theme_id
        for merge in merges
        for theme_id in (merge.get("source_theme_id", ""), merge.get("target_theme_id", ""))
    }
    themes = {
        theme.id
        for theme in session.scalars(
            select(Theme).where(Theme.dataset_id == dataset_id, Theme.id.in_(theme_ids))
        ).all()
    }
    source_to_target: dict[str, str] = {}
    ordered_source_ids: list[str] = []
    for merge in merges:
        source_theme_id = clean_text(merge.get("source_theme_id", ""))
        target_theme_id = clean_text(merge.get("target_theme_id", ""))
        if source_theme_id not in themes:
            raise ThemeLookupNotFoundError("Source theme not found.")
        if target_theme_id not in themes:
            raise ThemeLookupNotFoundError("Target theme not found.")
        if source_theme_id == target_theme_id:
            raise ThemeCurationValidationError("Source and target theme IDs must be different.")
        previous_target = source_to_target.get(source_theme_id)
        if previous_target is None:
            source_to_target[source_theme_id] = target_theme_id
            ordered_source_ids.append(source_theme_id)
        elif previous_target != target_theme_id:
            raise ThemeCurationValidationError(
                "A source theme can only be merged into one target within the same validation batch."
            )

    resolved_target_ids: dict[str, str] = {}

    def resolve_target(source_theme_id: str, trail: tuple[str, ...]) -> str:
        if source_theme_id in resolved_target_ids:
            return resolved_target_ids[source_theme_id]
        if source_theme_id in trail:
            raise ThemeCurationValidationError("Pending merge decisions create a cycle and cannot be validated together.")
        target_theme_id = source_to_target[source_theme_id]
        if target_theme_id in source_to_target:
            target_theme_id = resolve_target(target_theme_id, (*trail, source_theme_id))
        resolved_target_ids[source_theme_id] = target_theme_id
        return target_theme_id

    return [
        {
            "source_theme_id": source_theme_id,
            "target_theme_id": resolve_target(source_theme_id, ()),
        }
        for source_theme_id in ordered_source_ids
    ]


def _apply_theme_merge(
    session: Session,
    *,
    dataset_id: str,
    source_theme_id: str,
    target_theme_id: str,
) -> set[str]:
    source_links = list(
        session.scalars(
            select(StoryTheme)
            .join(Story, Story.id == StoryTheme.story_id)
            .where(
                StoryTheme.theme_id == source_theme_id,
                Story.dataset_id == dataset_id,
            )
        ).all()
    )
    source_story_ids = [link.story_id for link in source_links]
    target_story_ids = set()
    if source_story_ids:
        target_story_ids = set(
            session.scalars(
                select(StoryTheme.story_id).where(
                    StoryTheme.theme_id == target_theme_id,
                    StoryTheme.story_id.in_(source_story_ids),
                )
            ).all()
        )
    for link in source_links:
        if link.story_id in target_story_ids:
            session.delete(link)
        else:
            link.theme_id = target_theme_id
    session.flush()
    remaining_source_links = session.scalar(
        select(func.count()).select_from(StoryTheme).where(StoryTheme.theme_id == source_theme_id)
    )
    if remaining_source_links:
        raise ThemeCurationConflictError("Source theme still has assignments and cannot be deleted.")
    source_theme = session.scalar(
        select(Theme).where(Theme.id == source_theme_id, Theme.dataset_id == dataset_id)
    )
    if source_theme is not None:
        _delete_theme_artifacts(session, source_theme.id)
        session.delete(source_theme)
    return set(source_story_ids)


def _touch_theme_stories(session: Session, story_ids: Iterable[str]) -> None:
    ids = list(set(story_ids))
    if not ids:
        return
    stories = session.scalars(
        select(Story)
        .where(Story.id.in_(ids))
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
            selectinload(Story.theme_links).selectinload(StoryTheme.theme),
        )
    ).all()
    for story in stories:
        sync_story_derived_fields(story)
        story.version += 1


def _refresh_theme_cached_story_count(session: Session, theme_id: str) -> None:
    theme = session.get(Theme, theme_id)
    if theme is None:
        return
    theme.cached_story_count = int(
        session.scalar(select(func.count()).select_from(StoryTheme).where(StoryTheme.theme_id == theme_id)) or 0
    )


def _delete_theme_artifacts(session: Session, theme_id: str) -> None:
    session.execute(
        delete(TermSimilarityCache).where(
            TermSimilarityCache.term_kind == TermKind.THEME,
            or_(
                TermSimilarityCache.source_term_id == theme_id,
                TermSimilarityCache.target_term_id == theme_id,
            ),
        )
    )
    session.execute(delete(TermEmbedding).where(TermEmbedding.theme_id == theme_id))


def _serialize_theme_summary(theme: Theme) -> dict:
    return {
        "id": theme.id,
        "version": theme.version,
        "text": theme.text,
        "confirmation_status": theme.confirmation_status.value,
        "story_count": int(theme.cached_story_count or 0),
    }
