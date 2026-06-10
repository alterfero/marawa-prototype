from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.parsing import clean_text
from app.db.models import (
    AssignmentStatus,
    Dataset,
    DatasetStatus,
    Story,
    StoryKeyword,
    StoryTrope,
    StoryTropeOrigin,
    TermEmbedding,
    TermKind,
    TermSimilarityCache,
    Trope,
)
from app.services.jobs import queue_job
from app.services.stories import sync_story_derived_fields


class CurationError(ValueError):
    """Base error for trope curation operations."""


class CurationNotFoundError(CurationError):
    """Raised when a requested trope does not exist."""


class CurationConflictError(CurationError):
    """Raised when a curation action is blocked by current data state."""


class CurationValidationError(CurationError):
    """Raised when a curation request is invalid."""


def list_canonical_tropes(
    session: Session,
    *,
    unused_only: bool = False,
    query: str | None = None,
    limit: int = 100,
) -> list[dict]:
    query_text = clean_text(query) if query is not None else ""
    story_count_subquery = (
        select(
            StoryTrope.trope_id.label("trope_id"),
            func.count(func.distinct(StoryTrope.story_id)).label("story_count"),
        )
        .group_by(StoryTrope.trope_id)
        .subquery()
    )

    statement = (
        select(
            Trope.id,
            Trope.text,
            func.coalesce(story_count_subquery.c.story_count, 0).label("story_count"),
        )
        .select_from(Trope)
        .outerjoin(story_count_subquery, story_count_subquery.c.trope_id == Trope.id)
    )
    if query_text:
        statement = statement.where(func.lower(Trope.text).contains(query_text.lower()))
    if unused_only:
        statement = statement.where(func.coalesce(story_count_subquery.c.story_count, 0) == 0)

    rows = session.execute(
        statement.order_by(Trope.text.asc(), Trope.id.asc()).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "text": row.text,
            "story_count": int(row.story_count or 0),
        }
        for row in rows
    ]


def list_near_duplicate_tropes(session: Session, *, model_name: str) -> dict:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return {
            "items": [],
            "artifact_version": None,
            "model_name": model_name,
            "total": 0,
        }

    active_trope_ids = set(
        session.scalars(
            select(StoryTrope.trope_id).join(Story).where(Story.dataset_id == active_dataset.id)
        ).all()
    )
    if len(active_trope_ids) < 2:
        return {
            "items": [],
            "artifact_version": None,
            "model_name": model_name,
            "total": 0,
        }

    artifact_version = session.scalar(
        select(func.max(TermSimilarityCache.artifact_version)).where(
            TermSimilarityCache.term_kind == TermKind.TROPE,
            TermSimilarityCache.model_name == model_name,
        )
    )
    if artifact_version is None:
        return {
            "items": [],
            "artifact_version": None,
            "model_name": model_name,
            "total": 0,
        }

    entries = list(
        session.scalars(
            select(TermSimilarityCache).where(
                TermSimilarityCache.term_kind == TermKind.TROPE,
                TermSimilarityCache.model_name == model_name,
                TermSimilarityCache.artifact_version == artifact_version,
            )
        ).all()
    )
    if not entries:
        return {
            "items": [],
            "artifact_version": int(artifact_version),
            "model_name": model_name,
            "total": 0,
        }

    trope_ids = {
        term_id
        for entry in entries
        for term_id in (entry.source_term_id, entry.target_term_id)
        if term_id in active_trope_ids
    }
    active_story_counts = {
        row.trope_id: int(row.story_count)
        for row in session.execute(
            select(
                StoryTrope.trope_id.label("trope_id"),
                func.count(func.distinct(Story.id)).label("story_count"),
            )
            .select_from(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(
                Story.dataset_id == active_dataset.id,
                StoryTrope.trope_id.in_(trope_ids) if trope_ids else False,
            )
            .group_by(StoryTrope.trope_id)
        ).all()
    }
    tropes_by_id = {trope.id: trope for trope in session.scalars(select(Trope).where(Trope.id.in_(trope_ids))).all()}

    pair_map: dict[tuple[str, str], dict] = {}
    for entry in entries:
        if entry.source_term_id not in active_trope_ids or entry.target_term_id not in active_trope_ids:
            continue
        source_trope = tropes_by_id.get(entry.source_term_id)
        target_trope = tropes_by_id.get(entry.target_term_id)
        if source_trope is None or target_trope is None:
            continue

        stable_tropes = sorted(
            [source_trope, target_trope],
            key=lambda trope: (trope.text.lower(), trope.id),
        )
        pair_key = (stable_tropes[0].id, stable_tropes[1].id)
        candidate = {
            "source_trope": {
                "id": stable_tropes[0].id,
                "text": stable_tropes[0].text,
                "story_count": active_story_counts.get(stable_tropes[0].id, 0),
            },
            "target_trope": {
                "id": stable_tropes[1].id,
                "text": stable_tropes[1].text,
                "story_count": active_story_counts.get(stable_tropes[1].id, 0),
            },
            "similarity_score": float(entry.similarity_score),
            "metadata": entry.metadata_json or {},
        }
        existing = pair_map.get(pair_key)
        if existing is None or candidate["similarity_score"] > existing["similarity_score"]:
            pair_map[pair_key] = candidate

    items = sorted(
        pair_map.values(),
        key=lambda item: (
            -item["similarity_score"],
            item["source_trope"]["text"].lower(),
            item["target_trope"]["text"].lower(),
        ),
    )
    return {
        "items": items,
        "artifact_version": int(artifact_version),
        "model_name": model_name,
        "total": len(items),
    }


def merge_tropes(
    session: Session,
    *,
    source_trope_id: str,
    target_trope_id: str,
) -> tuple[Dataset, dict, object]:
    active_dataset = _require_active_dataset(session)
    if source_trope_id == target_trope_id:
        raise CurationValidationError("Source and target trope IDs must be different.")

    source_trope = session.get(Trope, source_trope_id)
    if source_trope is None:
        raise CurationNotFoundError("Source trope not found.")
    target_trope = session.get(Trope, target_trope_id)
    if target_trope is None:
        raise CurationNotFoundError("Target trope not found.")

    source_links = list(
        session.scalars(
            select(StoryTrope)
            .where(StoryTrope.trope_id == source_trope_id)
            .options(selectinload(StoryTrope.story))
        ).all()
    )
    source_story_ids = [link.story_id for link in source_links]
    target_links_by_story = {}
    if source_story_ids:
        target_links_by_story = {
            link.story_id: link
            for link in session.scalars(
                select(StoryTrope).where(
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
    session.expire_all()

    affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
    _refresh_trope_cached_story_count(session, target_trope_id)

    remaining_source_links = session.scalar(
        select(func.count()).select_from(StoryTrope).where(StoryTrope.trope_id == source_trope_id)
    )
    if remaining_source_links:
        raise CurationConflictError("Source trope still has assignments and cannot be deleted.")

    refreshed_source_trope = session.get(Trope, source_trope_id)
    if refreshed_source_trope is not None:
        _delete_trope_artifacts(session, source_trope_id)
        session.delete(refreshed_source_trope)

    _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)
    job = queue_job(
        session,
        job_type="full_rebuild",
        dataset_id=active_dataset.id,
        payload={
            "reason": "merge_tropes",
            "source_trope_id": source_trope_id,
            "target_trope_id": target_trope_id,
        },
    )
    session.commit()

    refreshed_active_dataset = session.get(Dataset, active_dataset.id)
    return refreshed_active_dataset, {
        "source_trope_id": source_trope_id,
        "target_trope_id": target_trope_id,
        "affected_story_count": len(affected_story_ids),
    }, job


def delete_trope(
    session: Session,
    *,
    trope_id: str,
    remove_from_all_stories: bool = False,
) -> tuple[Dataset, dict, object]:
    active_dataset = _require_active_dataset(session)
    trope = session.get(Trope, trope_id)
    if trope is None:
        raise CurationNotFoundError("Trope not found.")

    source_links = list(
        session.scalars(
            select(StoryTrope)
            .where(StoryTrope.trope_id == trope_id)
            .options(selectinload(StoryTrope.story))
        ).all()
    )
    if source_links and not remove_from_all_stories:
        raise CurationConflictError(
            "Trope still has story assignments. Set remove_from_all_stories=true to delete it everywhere."
        )

    affected_story_ids = {link.story_id for link in source_links}
    for link in source_links:
        session.delete(link)

    session.flush()
    session.expire_all()

    affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
    refreshed_trope = session.get(Trope, trope_id)
    if refreshed_trope is None:
        raise CurationNotFoundError("Trope not found.")

    remaining_source_links = session.scalar(
        select(func.count()).select_from(StoryTrope).where(StoryTrope.trope_id == trope_id)
    )
    if remaining_source_links:
        raise CurationConflictError("Trope still has story assignments and cannot be deleted.")

    _delete_trope_artifacts(session, trope_id)
    session.delete(refreshed_trope)

    _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)
    job = queue_job(
        session,
        job_type="full_rebuild",
        dataset_id=active_dataset.id,
        payload={
            "reason": "delete_trope",
            "trope_id": trope_id,
            "remove_from_all_stories": remove_from_all_stories,
        },
    )
    session.commit()

    refreshed_active_dataset = session.get(Dataset, active_dataset.id)
    return refreshed_active_dataset, {
        "deleted_trope_id": trope_id,
        "affected_story_count": len(affected_story_ids),
    }, job


def _get_active_dataset(session: Session) -> Dataset | None:
    return session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))


def _require_active_dataset(session: Session) -> Dataset:
    dataset = _get_active_dataset(session)
    if dataset is None:
        raise CurationConflictError("No active dataset is available for trope curation.")
    return dataset


def _touch_affected_stories(session: Session, story_ids: Iterable[str]) -> set[str]:
    story_ids = list(story_ids)
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


def _merge_story_trope_metadata(target_link: StoryTrope, source_link: StoryTrope) -> None:
    if source_link.position is not None and (target_link.position is None or source_link.position < target_link.position):
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


def _delete_trope_artifacts(session: Session, trope_id: str) -> None:
    session.execute(delete(TermSimilarityCache).where(
        TermSimilarityCache.term_kind == TermKind.TROPE,
        or_(
            TermSimilarityCache.source_term_id == trope_id,
            TermSimilarityCache.target_term_id == trope_id,
        ),
    ))
    session.execute(delete(TermEmbedding).where(TermEmbedding.trope_id == trope_id))


def _bump_dataset_versions(session: Session, active_dataset_id: str, affected_dataset_ids: set[str]) -> None:
    dataset_ids = set(affected_dataset_ids) or {active_dataset_id}
    for dataset in session.scalars(select(Dataset).where(Dataset.id.in_(dataset_ids))).all():
        dataset.version += 1
