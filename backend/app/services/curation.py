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
from app.services.audit import record_audit_event
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
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return []

    query_text = clean_text(query) if query is not None else ""
    statement = (
        select(
            Trope.id,
            Trope.text,
            Trope.cached_story_count.label("story_count"),
        )
        .select_from(Trope)
        .where(Trope.dataset_id == active_dataset.id)
    )
    if query_text:
        statement = statement.where(func.lower(Trope.text).contains(query_text.lower()))
    if unused_only:
        statement = statement.where(Trope.cached_story_count == 0)

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
            or_(
                TermSimilarityCache.source_term_id.in_(active_trope_ids),
                TermSimilarityCache.target_term_id.in_(active_trope_ids),
            ),
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
    tropes_by_id = {
        trope.id: trope
        for trope in session.scalars(
            select(Trope).where(
                Trope.dataset_id == active_dataset.id,
                Trope.id.in_(trope_ids),
            )
        ).all()
    }

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
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict, object | None]:
    dataset, summary, job = validate_trope_merges(
        session,
        merges=[
            {
                "source_trope_id": source_trope_id,
                "target_trope_id": target_trope_id,
            }
        ],
        job_reason="merge_tropes",
        job_payload={
            "source_trope_id": source_trope_id,
            "target_trope_id": target_trope_id,
        },
        actor_user_id=actor_user_id,
    )
    return dataset, summary["applied_merges"][0], job


def validate_trope_merges(
    session: Session,
    *,
    merges: list[dict[str, str]],
    job_reason: str = "validate_trope_merges",
    job_payload: dict | None = None,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict, object]:
    active_dataset = _require_active_dataset(session)
    normalized_merges = _normalize_merge_requests(session, merges)

    affected_story_ids: set[str] = set()
    touched_target_ids: set[str] = set()
    merge_summaries: list[dict[str, int | str]] = []

    for merge_request in normalized_merges:
        merge_affected_story_ids = _apply_trope_merge(
            session,
            dataset_id=active_dataset.id,
            source_trope_id=merge_request["source_trope_id"],
            target_trope_id=merge_request["target_trope_id"],
        )
        affected_story_ids.update(merge_affected_story_ids)
        touched_target_ids.add(merge_request["target_trope_id"])
        merge_summaries.append(
            {
                "source_trope_id": merge_request["source_trope_id"],
                "target_trope_id": merge_request["target_trope_id"],
                "affected_story_count": len(merge_affected_story_ids),
            }
        )

    session.flush()
    session.expire_all()

    affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
    for target_trope_id in touched_target_ids:
        _refresh_trope_cached_story_count(session, target_trope_id)

    _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)
    full_job_payload = {
        "reason": job_reason,
        "merge_count": len(merge_summaries),
        "merges": [
            {
                "source_trope_id": merge_summary["source_trope_id"],
                "target_trope_id": merge_summary["target_trope_id"],
            }
            for merge_summary in merge_summaries
        ],
    }
    if job_payload:
        full_job_payload.update(job_payload)

    job = None
    record_audit_event(
        session,
        event_type="trope.merged" if len(merge_summaries) == 1 else "trope.batch_merged",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="tropes",
        subject_id=merge_summaries[0]["source_trope_id"] if len(merge_summaries) == 1 else None,
        payload={
            "merge_count": len(merge_summaries),
            "affected_story_count": len(affected_story_ids),
            "merges": [
                {
                    "source_trope_id": merge_summary["source_trope_id"],
                    "target_trope_id": merge_summary["target_trope_id"],
                }
                for merge_summary in merge_summaries
            ],
            "rebuild_queued": False,
        },
    )
    session.commit()

    refreshed_active_dataset = session.get(Dataset, active_dataset.id)
    return refreshed_active_dataset, {
        "applied_merges": merge_summaries,
        "merge_count": len(merge_summaries),
        "affected_story_count": len(affected_story_ids),
    }, job


def delete_trope(
    session: Session,
    *,
    trope_id: str,
    remove_from_all_stories: bool = False,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict, object | None]:
    active_dataset = _require_active_dataset(session)
    trope = session.scalar(
        select(Trope).where(
            Trope.id == trope_id,
            Trope.dataset_id == active_dataset.id,
        )
    )
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
    job = None
    record_audit_event(
        session,
        event_type="trope.deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="tropes",
        subject_id=trope_id,
        payload={
            "affected_story_count": len(affected_story_ids),
            "remove_from_all_stories": remove_from_all_stories,
            "rebuild_queued": False,
        },
    )
    session.commit()

    refreshed_active_dataset = session.get(Dataset, active_dataset.id)
    return refreshed_active_dataset, {
        "deleted_trope_id": trope_id,
        "affected_story_count": len(affected_story_ids),
    }, job


def _normalize_merge_requests(session: Session, merges: list[dict[str, str]]) -> list[dict[str, str]]:
    if not merges:
        raise CurationValidationError("At least one merge decision is required.")

    active_dataset = _require_active_dataset(session)
    _validate_merge_targets_exist(session, active_dataset.id, merges)

    source_to_target: dict[str, str] = {}
    ordered_source_ids: list[str] = []
    for merge_request in merges:
        source_trope_id = merge_request["source_trope_id"]
        target_trope_id = merge_request["target_trope_id"]
        if source_trope_id == target_trope_id:
            raise CurationValidationError("Source and target trope IDs must be different.")

        existing_target_id = source_to_target.get(source_trope_id)
        if existing_target_id is None:
            source_to_target[source_trope_id] = target_trope_id
            ordered_source_ids.append(source_trope_id)
            continue

        if existing_target_id != target_trope_id:
            raise CurationValidationError(
                "A source trope can only be merged into one target within the same validation batch."
            )

    resolved_target_ids: dict[str, str] = {}

    def resolve_target_id(source_trope_id: str, trail: tuple[str, ...]) -> str:
        cached_target_id = resolved_target_ids.get(source_trope_id)
        if cached_target_id is not None:
            return cached_target_id

        if source_trope_id in trail:
            raise CurationValidationError("Pending merge decisions create a cycle and cannot be validated together.")

        target_trope_id = source_to_target[source_trope_id]
        if target_trope_id in source_to_target:
            target_trope_id = resolve_target_id(target_trope_id, (*trail, source_trope_id))

        resolved_target_ids[source_trope_id] = target_trope_id
        return target_trope_id

    normalized_merges: list[dict[str, str]] = []
    for source_trope_id in ordered_source_ids:
        normalized_merges.append(
            {
                "source_trope_id": source_trope_id,
                "target_trope_id": resolve_target_id(source_trope_id, ()),
            }
        )
    return normalized_merges


def _validate_merge_targets_exist(session: Session, dataset_id: str, merges: list[dict[str, str]]) -> None:
    trope_ids = {
        trope_id
        for merge_request in merges
        for trope_id in (merge_request["source_trope_id"], merge_request["target_trope_id"])
    }
    existing_trope_ids = set(
        session.scalars(
            select(Trope.id).where(
                Trope.dataset_id == dataset_id,
                Trope.id.in_(trope_ids),
            )
        ).all()
    )

    for merge_request in merges:
        if merge_request["source_trope_id"] not in existing_trope_ids:
            raise CurationNotFoundError("Source trope not found.")
        if merge_request["target_trope_id"] not in existing_trope_ids:
            raise CurationNotFoundError("Target trope not found.")


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
            .where(StoryTrope.trope_id == source_trope_id)
            .where(Story.dataset_id == dataset_id)
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
                    StoryTrope.trope_id == target_trope_id,
                    StoryTrope.story_id.in_(source_story_ids),
                    Story.dataset_id == dataset_id,
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

    remaining_source_links = session.scalar(
        select(func.count()).select_from(StoryTrope).where(StoryTrope.trope_id == source_trope_id)
    )
    if remaining_source_links:
        raise CurationConflictError("Source trope still has assignments and cannot be deleted.")

    refreshed_source_trope = session.scalar(
        select(Trope).where(
            Trope.id == source_trope_id,
            Trope.dataset_id == dataset_id,
        )
    )
    if refreshed_source_trope is not None:
        _delete_trope_artifacts(session, source_trope_id)
        session.delete(refreshed_source_trope)

    return affected_story_ids


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
