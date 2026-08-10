from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.db.models import Dataset, DatasetStatus, Job, JobStatus


def _job_summary(job: Job) -> dict[str, str]:
    return {
        "id": job.id,
        "status": job.status.value,
        "job_type": job.job_type,
    }


def get_dataset_maintenance_status(session: Session) -> dict:
    """Return the shared write-lock state for dataset-scoped content.

    A staged replacement is also locked: edits to the outgoing active dataset
    would otherwise disappear when the staged dataset is promoted.
    """

    active_rebuild = session.scalar(
        select(Job)
        .where(
            Job.job_type.in_(("full_rebuild", "restore_snapshot")),
            Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
        )
        .order_by(
            case((Job.status == JobStatus.RUNNING, 0), else_=1),
            Job.created_at.asc(),
            Job.id.asc(),
        )
    )
    if active_rebuild is not None:
        target_dataset = session.get(Dataset, active_rebuild.dataset_id) if active_rebuild.dataset_id else None
        state = active_rebuild.status.value
        action = "Dataset recovery" if active_rebuild.job_type == "restore_snapshot" else "Dataset rebuild"
        return {
            "active": True,
            "state": state,
            "message": f"{action} is {state}; dataset changes are temporarily paused.",
            "job": _job_summary(active_rebuild),
            "target_dataset_version": target_dataset.version if target_dataset is not None else None,
        }

    staged_dataset = session.scalar(
        select(Dataset)
        .where(Dataset.status == DatasetStatus.STAGED)
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
    )
    if staged_dataset is not None:
        return {
            "active": True,
            "state": "staged",
            "message": "A replacement dataset is staged; dataset changes are paused until it is rebuilt or replaced.",
            "job": None,
            "target_dataset_version": staged_dataset.version,
        }

    return {
        "active": False,
        "state": "available",
        "message": None,
        "job": None,
        "target_dataset_version": None,
    }
