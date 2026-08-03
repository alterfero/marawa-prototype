from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.parsing import clean_text
from app.db.models import (
    AssignmentStatus,
    Dataset,
    DatasetStatus,
    Keyword,
    KeywordConfirmationStatus,
    Story,
    StoryKeyword,
    StoryTheme,
    StoryTrope,
    StoryTropeOrigin,
    TermEmbedding,
    TermKind,
    TermSimilarityCache,
    Trope,
    TropeConfirmationStatus,
)
from app.services.audit import record_audit_event
from app.services.stories import sync_story_derived_fields

if TYPE_CHECKING:
    from app.services.search_service import SearchService


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
    include_story_ids: bool = False,
) -> list[dict]:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return []

    query_text = clean_text(query) if query is not None else ""
    statement = (
        select(
            Trope.id,
            Trope.text,
            Trope.version,
            Trope.confirmation_status,
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
    story_ids_by_trope_id: dict[str, list[str]] = {}
    if include_story_ids and rows:
        trope_ids = [row.id for row in rows]
        story_ids_by_trope_id = {trope_id: [] for trope_id in trope_ids}
        story_rows = session.execute(
            select(
                StoryTrope.trope_id.label("trope_id"),
                Story.id.label("story_id"),
            )
            .select_from(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(
                Story.dataset_id == active_dataset.id,
                StoryTrope.trope_id.in_(trope_ids),
            )
            .order_by(
                StoryTrope.trope_id.asc(),
                case((Story.source_row_number.is_(None), 1), else_=0),
                Story.source_row_number.asc(),
                Story.created_at.asc(),
                Story.id.asc(),
            )
        ).all()
        for story_row in story_rows:
            story_ids_by_trope_id.setdefault(story_row.trope_id, []).append(story_row.story_id)

    return [
        {
            "id": row.id,
            "text": row.text,
            "version": int(row.version or 1),
            "confirmation_status": row.confirmation_status.value,
            "story_count": int(row.story_count or 0),
            "story_ids": story_ids_by_trope_id.get(row.id, []),
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
        display_tropes = stable_tropes
        if (
            stable_tropes[0].confirmation_status == TropeConfirmationStatus.CANONICAL
            and stable_tropes[1].confirmation_status == TropeConfirmationStatus.UNCONFIRMED
        ):
            display_tropes = [stable_tropes[1], stable_tropes[0]]
        candidate = {
            "source_trope": {
                "id": display_tropes[0].id,
                "version": display_tropes[0].version,
                "text": display_tropes[0].text,
                "confirmation_status": display_tropes[0].confirmation_status.value,
                "story_count": active_story_counts.get(display_tropes[0].id, 0),
            },
            "target_trope": {
                "id": display_tropes[1].id,
                "version": display_tropes[1].version,
                "text": display_tropes[1].text,
                "confirmation_status": display_tropes[1].confirmation_status.value,
                "story_count": active_story_counts.get(display_tropes[1].id, 0),
            },
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
                item["source_trope"]["confirmation_status"] == TropeConfirmationStatus.CANONICAL.value
                and item["target_trope"]["confirmation_status"] == TropeConfirmationStatus.CANONICAL.value
            )
        ],
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


def list_similar_unconfirmed_tropes(
    session: Session,
    *,
    source_trope_id: str,
    minimum_similarity: float,
    search_service: SearchService,
    include_canonical: bool = False,
) -> dict:
    """List tropes similar to one selected trope.

    This deliberately queries the current embedding vectors instead of the
    near-duplicate cache. The cache only keeps very high-scoring pairs for the
    Curation view, while trope management exposes a curator-selected threshold.
    By default, only unconfirmed candidates are returned for compatibility with
    the existing curation workflow. Callers can opt in to canonical candidates.
    """
    if not 0.0 <= minimum_similarity <= 1.0:
        raise CurationValidationError("Minimum similarity must be between 0 and 1.")

    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise CurationNotFoundError("Selected trope not found.")

    source_trope = session.scalar(
        select(Trope).where(
            Trope.id == source_trope_id,
            Trope.dataset_id == active_dataset.id,
        )
    )
    if source_trope is None:
        raise CurationNotFoundError("Selected trope not found.")

    candidate_statement = select(Trope).where(
        Trope.dataset_id == active_dataset.id,
        Trope.id != source_trope.id,
    )
    if not include_canonical:
        candidate_statement = candidate_statement.where(
            Trope.confirmation_status == TropeConfirmationStatus.UNCONFIRMED
        )

    candidate_tropes = list(
        session.scalars(candidate_statement.order_by(Trope.text.asc(), Trope.id.asc())).all()
    )
    artifact_version, scores_by_trope_id = search_service.get_similar_trope_scores(
        session,
        source_trope.id,
        [trope.id for trope in candidate_tropes],
        minimum_score=minimum_similarity,
    )

    items = sorted(
        [
            {
                "id": trope.id,
                "version": trope.version,
                "text": trope.text,
                "confirmation_status": trope.confirmation_status.value,
                "story_count": int(trope.cached_story_count or 0),
                "similarity_score": scores_by_trope_id[trope.id],
            }
            for trope in candidate_tropes
            if trope.id in scores_by_trope_id
        ],
        key=lambda item: (-item["similarity_score"], item["text"].lower(), item["id"]),
    )
    return {
        "source_trope_id": source_trope.id,
        "items": items,
        "artifact_version": artifact_version,
        "model_name": search_service.model_name,
        "minimum_similarity": minimum_similarity,
        "total": len(items),
    }


def list_near_duplicate_keywords(session: Session, *, model_name: str) -> dict:
    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        return _empty_near_duplicate_result(model_name)

    active_keyword_ids = set(
        session.scalars(
            select(StoryKeyword.keyword_id).join(Story).where(Story.dataset_id == active_dataset.id)
        ).all()
    )
    if len(active_keyword_ids) < 2:
        return _empty_near_duplicate_result(model_name)

    artifact_version = session.scalar(
        select(func.max(TermSimilarityCache.artifact_version)).where(
            TermSimilarityCache.term_kind == TermKind.KEYWORD,
            TermSimilarityCache.model_name == model_name,
            or_(
                TermSimilarityCache.source_term_id.in_(active_keyword_ids),
                TermSimilarityCache.target_term_id.in_(active_keyword_ids),
            ),
        )
    )
    if artifact_version is None:
        return _empty_near_duplicate_result(model_name)

    entries = list(
        session.scalars(
            select(TermSimilarityCache).where(
                TermSimilarityCache.term_kind == TermKind.KEYWORD,
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

    keyword_ids = {
        term_id
        for entry in entries
        for term_id in (entry.source_term_id, entry.target_term_id)
        if term_id in active_keyword_ids
    }
    keywords_by_id = {
        keyword.id: keyword
        for keyword in session.scalars(
            select(Keyword).where(
                Keyword.dataset_id == active_dataset.id,
                Keyword.id.in_(keyword_ids),
            )
        ).all()
    }

    pair_map: dict[tuple[str, str], dict] = {}
    for entry in entries:
        if entry.source_term_id not in active_keyword_ids or entry.target_term_id not in active_keyword_ids:
            continue
        source_keyword = keywords_by_id.get(entry.source_term_id)
        target_keyword = keywords_by_id.get(entry.target_term_id)
        if source_keyword is None or target_keyword is None:
            continue

        stable_keywords = sorted(
            [source_keyword, target_keyword],
            key=lambda keyword: (keyword.text.lower(), keyword.id),
        )
        pair_key = (stable_keywords[0].id, stable_keywords[1].id)
        display_keywords = stable_keywords
        if (
            stable_keywords[0].confirmation_status == KeywordConfirmationStatus.CANONICAL
            and stable_keywords[1].confirmation_status == KeywordConfirmationStatus.UNCONFIRMED
        ):
            display_keywords = [stable_keywords[1], stable_keywords[0]]
        candidate = {
            "source_keyword": _serialize_keyword_summary(display_keywords[0]),
            "target_keyword": _serialize_keyword_summary(display_keywords[1]),
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
                item["source_keyword"]["confirmation_status"] == KeywordConfirmationStatus.CANONICAL.value
                and item["target_keyword"]["confirmation_status"] == KeywordConfirmationStatus.CANONICAL.value
            )
        ],
        key=lambda item: (
            -item["similarity_score"],
            item["source_keyword"]["text"].lower(),
            item["target_keyword"]["text"].lower(),
        ),
    )
    return {
        "items": items,
        "artifact_version": int(artifact_version),
        "model_name": model_name,
        "total": len(items),
    }


def list_similar_unconfirmed_keywords(
    session: Session,
    *,
    source_keyword_id: str,
    minimum_similarity: float,
    search_service: SearchService,
    include_canonical: bool = False,
) -> dict:
    if not 0.0 <= minimum_similarity <= 1.0:
        raise CurationValidationError("Minimum similarity must be between 0 and 1.")

    active_dataset = _get_active_dataset(session)
    if active_dataset is None:
        raise CurationNotFoundError("Selected keyword not found.")

    source_keyword = session.scalar(
        select(Keyword).where(
            Keyword.id == source_keyword_id,
            Keyword.dataset_id == active_dataset.id,
        )
    )
    if source_keyword is None:
        raise CurationNotFoundError("Selected keyword not found.")

    candidate_statement = select(Keyword).where(
        Keyword.dataset_id == active_dataset.id,
        Keyword.id != source_keyword.id,
    )
    if not include_canonical:
        candidate_statement = candidate_statement.where(
            Keyword.confirmation_status == KeywordConfirmationStatus.UNCONFIRMED
        )
    candidate_keywords = list(
        session.scalars(candidate_statement.order_by(Keyword.text.asc(), Keyword.id.asc())).all()
    )
    artifact_version, scores_by_keyword_id = search_service.get_similar_keyword_scores(
        session,
        source_keyword.id,
        [keyword.id for keyword in candidate_keywords],
        minimum_score=minimum_similarity,
    )
    items = sorted(
        [
            {
                **_serialize_keyword_summary(keyword),
                "similarity_score": scores_by_keyword_id[keyword.id],
            }
            for keyword in candidate_keywords
            if keyword.id in scores_by_keyword_id
        ],
        key=lambda item: (-item["similarity_score"], item["text"].lower(), item["id"]),
    )
    return {
        "source_keyword_id": source_keyword.id,
        "items": items,
        "artifact_version": artifact_version,
        "model_name": search_service.model_name,
        "minimum_similarity": minimum_similarity,
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


def delete_unused_tropes(
    session: Session,
    *,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict, object | None]:
    """Delete every unassigned trope in the active dataset in one transaction."""
    active_dataset = _require_active_dataset(session)
    unused_tropes = list(
        session.scalars(
            select(Trope).where(
                Trope.dataset_id == active_dataset.id,
                Trope.cached_story_count == 0,
                ~Trope.story_links.any(),
            )
        ).all()
    )
    if not unused_tropes:
        return active_dataset, {"deleted_trope_count": 0}, None

    for trope in unused_tropes:
        _delete_trope_artifacts(session, trope.id)
        session.delete(trope)

    _bump_dataset_versions(session, active_dataset.id, set())
    record_audit_event(
        session,
        event_type="trope.unused_batch_deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="tropes",
        payload={
            "deleted_trope_count": len(unused_tropes),
            "rebuild_queued": False,
        },
    )
    session.commit()

    refreshed_active_dataset = session.get(Dataset, active_dataset.id)
    return refreshed_active_dataset, {"deleted_trope_count": len(unused_tropes)}, None


def merge_keywords(
    session: Session,
    *,
    source_keyword_id: str,
    target_keyword_id: str,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict, object | None]:
    dataset, summary, job = validate_keyword_merges(
        session,
        merges=[
            {
                "source_keyword_id": source_keyword_id,
                "target_keyword_id": target_keyword_id,
            }
        ],
        job_reason="merge_keywords",
        job_payload={
            "source_keyword_id": source_keyword_id,
            "target_keyword_id": target_keyword_id,
        },
        actor_user_id=actor_user_id,
    )
    return dataset, summary["applied_merges"][0], job


def validate_keyword_merges(
    session: Session,
    *,
    merges: list[dict[str, str]],
    job_reason: str = "validate_keyword_merges",
    job_payload: dict | None = None,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict, object | None]:
    active_dataset = _require_active_dataset(session)
    normalized_merges = _normalize_keyword_merge_requests(session, merges)

    affected_story_ids: set[str] = set()
    touched_target_ids: set[str] = set()
    merge_summaries: list[dict[str, int | str]] = []
    for merge_request in normalized_merges:
        affected_ids = _apply_keyword_merge(
            session,
            dataset_id=active_dataset.id,
            source_keyword_id=merge_request["source_keyword_id"],
            target_keyword_id=merge_request["target_keyword_id"],
        )
        affected_story_ids.update(affected_ids)
        touched_target_ids.add(merge_request["target_keyword_id"])
        merge_summaries.append(
            {
                "source_keyword_id": merge_request["source_keyword_id"],
                "target_keyword_id": merge_request["target_keyword_id"],
                "affected_story_count": len(affected_ids),
            }
        )

    session.flush()
    session.expire_all()
    affected_dataset_ids = _touch_affected_stories(session, affected_story_ids)
    for target_keyword_id in touched_target_ids:
        _refresh_keyword_cached_story_count(session, target_keyword_id)
    _bump_dataset_versions(session, active_dataset.id, affected_dataset_ids)

    record_audit_event(
        session,
        event_type="keyword.merged" if len(merge_summaries) == 1 else "keyword.batch_merged",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="keywords",
        subject_id=merge_summaries[0]["source_keyword_id"] if len(merge_summaries) == 1 else None,
        payload={
            "reason": job_reason,
            "merge_count": len(merge_summaries),
            "affected_story_count": len(affected_story_ids),
            "merges": [
                {
                    "source_keyword_id": merge_summary["source_keyword_id"],
                    "target_keyword_id": merge_summary["target_keyword_id"],
                }
                for merge_summary in merge_summaries
            ],
            "job_payload": job_payload or {},
            "rebuild_queued": False,
        },
    )
    session.commit()

    refreshed_active_dataset = session.get(Dataset, active_dataset.id)
    return refreshed_active_dataset, {
        "applied_merges": merge_summaries,
        "merge_count": len(merge_summaries),
        "affected_story_count": len(affected_story_ids),
    }, None


def delete_unused_keywords(
    session: Session,
    *,
    actor_user_id: str | None = None,
) -> tuple[Dataset, dict, object | None]:
    active_dataset = _require_active_dataset(session)
    unused_keywords = list(
        session.scalars(
            select(Keyword).where(
                Keyword.dataset_id == active_dataset.id,
                Keyword.cached_story_count == 0,
                ~Keyword.story_links.any(),
            )
        ).all()
    )
    if not unused_keywords:
        return active_dataset, {"deleted_keyword_count": 0}, None

    for keyword in unused_keywords:
        _delete_keyword_artifacts(session, keyword.id)
        session.delete(keyword)

    _bump_dataset_versions(session, active_dataset.id, set())
    record_audit_event(
        session,
        event_type="keyword.unused_batch_deleted",
        actor_user_id=actor_user_id,
        dataset_id=active_dataset.id,
        subject_table="keywords",
        payload={
            "deleted_keyword_count": len(unused_keywords),
            "rebuild_queued": False,
        },
    )
    session.commit()
    refreshed_active_dataset = session.get(Dataset, active_dataset.id)
    return refreshed_active_dataset, {"deleted_keyword_count": len(unused_keywords)}, None


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


def _normalize_keyword_merge_requests(session: Session, merges: list[dict[str, str]]) -> list[dict[str, str]]:
    if not merges:
        raise CurationValidationError("At least one merge decision is required.")

    active_dataset = _require_active_dataset(session)
    keyword_ids = {
        keyword_id
        for merge_request in merges
        for keyword_id in (merge_request["source_keyword_id"], merge_request["target_keyword_id"])
    }
    existing_keyword_ids = set(
        session.scalars(
            select(Keyword.id).where(
                Keyword.dataset_id == active_dataset.id,
                Keyword.id.in_(keyword_ids),
            )
        ).all()
    )

    source_to_target: dict[str, str] = {}
    ordered_source_ids: list[str] = []
    for merge_request in merges:
        source_keyword_id = merge_request["source_keyword_id"]
        target_keyword_id = merge_request["target_keyword_id"]
        if source_keyword_id not in existing_keyword_ids:
            raise CurationNotFoundError("Source keyword not found.")
        if target_keyword_id not in existing_keyword_ids:
            raise CurationNotFoundError("Target keyword not found.")
        if source_keyword_id == target_keyword_id:
            raise CurationValidationError("Source and target keyword IDs must be different.")

        existing_target_id = source_to_target.get(source_keyword_id)
        if existing_target_id is None:
            source_to_target[source_keyword_id] = target_keyword_id
            ordered_source_ids.append(source_keyword_id)
        elif existing_target_id != target_keyword_id:
            raise CurationValidationError(
                "A source keyword can only be merged into one target within the same validation batch."
            )

    resolved_target_ids: dict[str, str] = {}

    def resolve_target_id(source_keyword_id: str, trail: tuple[str, ...]) -> str:
        cached_target_id = resolved_target_ids.get(source_keyword_id)
        if cached_target_id is not None:
            return cached_target_id
        if source_keyword_id in trail:
            raise CurationValidationError("Pending merge decisions create a cycle and cannot be validated together.")

        target_keyword_id = source_to_target[source_keyword_id]
        if target_keyword_id in source_to_target:
            target_keyword_id = resolve_target_id(target_keyword_id, (*trail, source_keyword_id))
        resolved_target_ids[source_keyword_id] = target_keyword_id
        return target_keyword_id

    return [
        {
            "source_keyword_id": source_keyword_id,
            "target_keyword_id": resolve_target_id(source_keyword_id, ()),
        }
        for source_keyword_id in ordered_source_ids
    ]


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
                StoryKeyword.keyword_id == source_keyword_id,
                Story.dataset_id == dataset_id,
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
                    StoryKeyword.keyword_id == target_keyword_id,
                    StoryKeyword.story_id.in_(source_story_ids),
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
                StoryKeyword(
                    story_id=source_link.story_id,
                    keyword_id=target_keyword_id,
                    position=source_link.position,
                )
            )
        elif source_link.position is not None and (
            target_link.position is None or source_link.position < target_link.position
        ):
            target_link.position = source_link.position
        session.delete(source_link)

    session.flush()
    if session.scalar(select(func.count()).select_from(StoryKeyword).where(StoryKeyword.keyword_id == source_keyword_id)):
        raise CurationConflictError("Source keyword still has assignments and cannot be deleted.")

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


def _get_active_dataset(session: Session) -> Dataset | None:
    return session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))


def _empty_near_duplicate_result(model_name: str) -> dict:
    return {
        "items": [],
        "artifact_version": None,
        "model_name": model_name,
        "total": 0,
    }


def _serialize_keyword_summary(keyword: Keyword) -> dict:
    return {
        "id": keyword.id,
        "version": keyword.version,
        "text": keyword.text,
        "confirmation_status": keyword.confirmation_status.value,
        "story_count": int(keyword.cached_story_count or 0),
    }


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
            selectinload(Story.theme_links).selectinload(StoryTheme.theme),
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


def _refresh_keyword_cached_story_count(session: Session, keyword_id: str) -> None:
    keyword = session.get(Keyword, keyword_id)
    if keyword is None:
        return
    keyword.cached_story_count = int(
        session.scalar(select(func.count(func.distinct(StoryKeyword.story_id))).where(StoryKeyword.keyword_id == keyword_id))
        or 0
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


def _bump_dataset_versions(session: Session, active_dataset_id: str, affected_dataset_ids: set[str]) -> None:
    dataset_ids = set(affected_dataset_ids) or {active_dataset_id}
    for dataset in session.scalars(select(Dataset).where(Dataset.id.in_(dataset_ids))).all():
        dataset.version += 1
