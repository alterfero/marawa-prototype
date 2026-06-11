from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Dataset,
    DatasetStatus,
    Job,
    JobStatus,
    Keyword,
    Story,
    StoryKeyword,
    StoryTrope,
    TermEmbedding,
    TermKind,
    TermSimilarityCache,
    Trope,
)
from app.services.csv_io import import_csv_bytes
from app.services.jobs import queue_job


def _job_summary(job: Job | None) -> dict | None:
    return None if job is None else {"id": job.id, "status": job.status.value, "job_type": job.job_type}


def _embedding_status(
    session: Session,
    *,
    dataset: Dataset | None,
    trope_count: int,
    keyword_count: int,
    model_name: str,
) -> dict:
    latest_rebuild_job = session.scalar(
        select(Job)
        .where(Job.job_type == "full_rebuild")
        .order_by(Job.created_at.desc(), Job.id.desc())
    )

    if dataset is None:
        return {
            "state": "missing",
            "ready": False,
            "current": False,
            "model_name": model_name,
            "artifact_version": None,
            "rebuilt_dataset_version": None,
            "indexed_trope_count": 0,
            "indexed_keyword_count": 0,
            "last_built_at": None,
            "last_error_message": None
            if latest_rebuild_job is None or latest_rebuild_job.status != JobStatus.FAILED
            else latest_rebuild_job.error_message,
            "latest_rebuild_job": _job_summary(latest_rebuild_job),
        }

    latest_rebuild_job = session.scalar(
        select(Job)
        .where(
            Job.dataset_id == dataset.id,
            Job.job_type == "full_rebuild",
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
    )
    latest_successful_rebuild = session.scalar(
        select(Job)
        .where(
            Job.dataset_id == dataset.id,
            Job.job_type == "full_rebuild",
            Job.status == JobStatus.SUCCEEDED,
        )
        .order_by(Job.finished_at.desc(), Job.created_at.desc(), Job.id.desc())
    )

    artifact_version = None
    rebuilt_dataset_version = None
    indexed_trope_count = 0
    indexed_keyword_count = 0
    last_built_at = None
    actual_trope_embeddings = 0
    actual_keyword_embeddings = 0

    if latest_successful_rebuild is not None:
        result = latest_successful_rebuild.result_json or {}
        raw_artifact_version = result.get("artifact_version")
        raw_rebuilt_dataset_version = result.get("dataset_version")
        raw_tropes_indexed = result.get("tropes_indexed")
        raw_keywords_indexed = result.get("keywords_indexed")

        artifact_version = raw_artifact_version if isinstance(raw_artifact_version, int) else None
        rebuilt_dataset_version = (
            raw_rebuilt_dataset_version if isinstance(raw_rebuilt_dataset_version, int) else None
        )
        indexed_trope_count = raw_tropes_indexed if isinstance(raw_tropes_indexed, int) else 0
        indexed_keyword_count = raw_keywords_indexed if isinstance(raw_keywords_indexed, int) else 0
        last_built_at = latest_successful_rebuild.finished_at.isoformat() if latest_successful_rebuild.finished_at else None

        if artifact_version is not None:
            active_trope_ids = select(StoryTrope.trope_id).join(Story).where(Story.dataset_id == dataset.id).distinct()
            active_keyword_ids = (
                select(StoryKeyword.keyword_id).join(Story).where(Story.dataset_id == dataset.id).distinct()
            )
            actual_trope_embeddings = (
                session.scalar(
                    select(func.count(TermEmbedding.id)).where(
                        TermEmbedding.term_kind == TermKind.TROPE,
                        TermEmbedding.model_name == model_name,
                        TermEmbedding.artifact_version == artifact_version,
                        TermEmbedding.trope_id.in_(active_trope_ids),
                    )
                )
                or 0
            )
            actual_keyword_embeddings = (
                session.scalar(
                    select(func.count(TermEmbedding.id)).where(
                        TermEmbedding.term_kind == TermKind.KEYWORD,
                        TermEmbedding.model_name == model_name,
                        TermEmbedding.artifact_version == artifact_version,
                        TermEmbedding.keyword_id.in_(active_keyword_ids),
                    )
                )
                or 0
            )

    ready = latest_successful_rebuild is not None
    current = (
        ready
        and rebuilt_dataset_version == dataset.version
        and actual_trope_embeddings == trope_count
        and actual_keyword_embeddings == keyword_count
    )

    if current:
        state = "ready"
    elif latest_rebuild_job is not None and latest_rebuild_job.status == JobStatus.RUNNING:
        state = "running"
    elif latest_rebuild_job is not None and latest_rebuild_job.status == JobStatus.QUEUED:
        state = "queued"
    elif latest_rebuild_job is not None and latest_rebuild_job.status == JobStatus.FAILED:
        state = "failed"
    elif ready:
        state = "stale"
    else:
        state = "missing"

    return {
        "state": state,
        "ready": ready,
        "current": current,
        "model_name": model_name,
        "artifact_version": artifact_version,
        "rebuilt_dataset_version": rebuilt_dataset_version,
        "indexed_trope_count": int(indexed_trope_count),
        "indexed_keyword_count": int(indexed_keyword_count),
        "last_built_at": last_built_at,
        "last_error_message": None
        if latest_rebuild_job is None or latest_rebuild_job.status != JobStatus.FAILED
        else latest_rebuild_job.error_message,
        "latest_rebuild_job": _job_summary(latest_rebuild_job),
    }


def get_dataset_status(session: Session, *, model_name: str) -> dict:
    dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if dataset is None:
        latest_job = session.scalar(select(Job).order_by(Job.created_at.desc(), Job.id.desc()))
        return {
            "story_count": 0,
            "trope_count": 0,
            "keyword_count": 0,
            "active_dataset_version": None,
            "latest_job": _job_summary(latest_job),
            "embedding_status": _embedding_status(
                session,
                dataset=None,
                trope_count=0,
                keyword_count=0,
                model_name=model_name,
            ),
        }

    story_count = session.scalar(select(func.count(Story.id)).where(Story.dataset_id == dataset.id)) or 0
    trope_count = (
        session.scalar(
            select(func.count(func.distinct(StoryTrope.trope_id)))
            .select_from(StoryTrope)
            .join(Story, Story.id == StoryTrope.story_id)
            .where(Story.dataset_id == dataset.id)
        )
        or 0
    )
    keyword_count = (
        session.scalar(
            select(func.count(func.distinct(StoryKeyword.keyword_id)))
            .select_from(StoryKeyword)
            .join(Story, Story.id == StoryKeyword.story_id)
            .where(Story.dataset_id == dataset.id)
        )
        or 0
    )
    latest_job = session.scalar(
        select(Job).where(Job.dataset_id == dataset.id).order_by(Job.created_at.desc(), Job.id.desc())
    )
    return {
        "story_count": int(story_count),
        "trope_count": int(trope_count),
        "keyword_count": int(keyword_count),
        "active_dataset_version": dataset.version,
        "latest_job": _job_summary(latest_job),
        "embedding_status": _embedding_status(
            session,
            dataset=dataset,
            trope_count=int(trope_count),
            keyword_count=int(keyword_count),
            model_name=model_name,
        ),
    }


def upload_dataset_csv(
    session: Session,
    csv_bytes: bytes,
    *,
    source_filename: str | None = None,
) -> tuple[Dataset, Job]:
    dataset = import_csv_bytes(session, csv_bytes, source_filename=source_filename)
    job = queue_job(
        session,
        job_type="full_rebuild",
        dataset_id=dataset.id,
        payload={
            "reason": "dataset_upload",
            "source_filename": source_filename,
        },
    )
    session.commit()
    session.refresh(dataset)
    session.refresh(job)
    return dataset, job


def clear_dataset_data(session: Session) -> None:
    session.execute(delete(TermSimilarityCache))
    session.execute(delete(TermEmbedding))
    session.execute(delete(StoryTrope))
    session.execute(delete(StoryKeyword))
    session.execute(delete(Job))
    session.execute(delete(Story))
    session.execute(delete(Trope))
    session.execute(delete(Keyword))
    session.execute(delete(Dataset))
    session.commit()
