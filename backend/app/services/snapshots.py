"""Logical dataset snapshots and staged Time Machine recovery jobs."""

from __future__ import annotations

import gzip
import hashlib
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Dataset,
    DatasetSnapshot,
    DatasetSnapshotStatus,
    DatasetStatus,
    Job,
    JobStatus,
    Keyword,
    Story,
    Theme,
    Trope,
)
from app.core.parsing import clean_text, normalize_text
from app.services.audit import record_audit_event
from app.services.csv_io import (
    CSVImportValidationError,
    DatasetComparisonValues,
    export_dataset_to_csv_bytes,
    extract_dataset_comparison_values,
    import_csv_bytes,
)
from app.services.jobs import queue_job
from app.services.snapshot_storage import SnapshotStore, SnapshotStorageError


class DatasetSnapshotNotFoundError(ValueError):
    """Raised when a requested snapshot does not exist."""


class DatasetSnapshotRestoreUnavailableError(ValueError):
    """Raised when a snapshot cannot safely be restored."""


class DatasetSnapshotComparisonUnavailableError(ValueError):
    """Raised when a snapshot cannot be loaded for its checkpoint preview."""


def _snapshot_counts(session: Session, dataset_id: str) -> dict[str, int]:
    return {
        "story_count": int(session.scalar(select(func.count(Story.id)).where(Story.dataset_id == dataset_id)) or 0),
        "trope_count": int(session.scalar(select(func.count(Trope.id)).where(Trope.dataset_id == dataset_id)) or 0),
        "theme_count": int(session.scalar(select(func.count(Theme.id)).where(Theme.dataset_id == dataset_id)) or 0),
        "keyword_count": int(session.scalar(select(func.count(Keyword.id)).where(Keyword.dataset_id == dataset_id)) or 0),
    }


def _current_dataset_comparison_values(session: Session, dataset_id: str) -> DatasetComparisonValues:
    stories = session.scalars(select(Story).where(Story.dataset_id == dataset_id)).all()
    tropes = session.scalars(select(Trope).where(Trope.dataset_id == dataset_id)).all()
    themes = session.scalars(select(Theme).where(Theme.dataset_id == dataset_id)).all()
    keywords = session.scalars(select(Keyword).where(Keyword.dataset_id == dataset_id)).all()
    return DatasetComparisonValues(
        story_titles=tuple(clean_text(story.fields_json.get("Story title (Eng)", "")) for story in stories),
        tropes=tuple(trope.text for trope in tropes),
        themes=tuple(theme.text for theme in themes),
        keywords=tuple(keyword.text for keyword in keywords),
    )


def _named_difference(
    checkpoint_values: Iterable[str],
    current_values: Iterable[str],
    *,
    unnamed_label: str = "Untitled story",
) -> dict[str, list[dict[str, int | str]]]:
    """Return normalized value differences while retaining the human label.

    Story titles are allowed to repeat.  Keeping an occurrence count makes a
    duplicate addition or removal visible instead of silently collapsing it.
    """

    def index(values: Iterable[str]) -> tuple[Counter[str], dict[str, str]]:
        counts: Counter[str] = Counter()
        labels: dict[str, str] = {}
        for value in values:
            text = clean_text(value)
            marker = normalize_text(text)
            counts[marker] += 1
            labels.setdefault(marker, text or unnamed_label)
        return counts, labels

    checkpoint_counts, checkpoint_labels = index(checkpoint_values)
    current_counts, current_labels = index(current_values)

    def differences(
        source: Counter[str],
        other: Counter[str],
        labels: dict[str, str],
    ) -> list[dict[str, int | str]]:
        result = [
            {"text": labels[marker], "count": source[marker] - other.get(marker, 0)}
            for marker in source
            if source[marker] > other.get(marker, 0)
        ]
        return sorted(result, key=lambda item: (str(item["text"]).casefold(), int(item["count"])))

    return {
        "current_only": differences(current_counts, checkpoint_counts, current_labels),
        "checkpoint_only": differences(checkpoint_counts, current_counts, checkpoint_labels),
    }


class DatasetSnapshotService:
    def __init__(self, *, storage: SnapshotStore, retention_count: int, bucket_prefix: str) -> None:
        self.storage = storage
        self.retention_count = retention_count
        self.bucket_prefix = bucket_prefix.strip("/")

    def capture(
        self,
        session: Session,
        *,
        dataset: Dataset,
        source_job: Job | None = None,
        created_by_user_id: str | None = None,
        reason: str = "full_rebuild",
        protect_snapshot_ids: Iterable[str] = (),
    ) -> DatasetSnapshot:
        if source_job is not None:
            existing = session.scalar(
                select(DatasetSnapshot).where(DatasetSnapshot.source_job_id == source_job.id)
            )
            if existing is not None:
                if existing.status != DatasetSnapshotStatus.READY:
                    raise SnapshotStorageError("A prior snapshot attempt for this rebuild is incomplete.")
                return existing

        next_sequence = int(session.scalar(select(func.max(DatasetSnapshot.sequence))) or 0) + 1
        snapshot = DatasetSnapshot(
            sequence=next_sequence,
            status=DatasetSnapshotStatus.CREATING,
            reason=reason,
            dataset_id=dataset.id,
            source_job_id=source_job.id if source_job is not None else None,
            created_by_user_id=created_by_user_id,
            source_dataset_version=dataset.version,
            source_filename=dataset.source_filename,
            object_key="pending",
            **_snapshot_counts(session, dataset.id),
        )
        session.add(snapshot)
        session.flush()
        snapshot.object_key = self._object_key(snapshot)

        csv_bytes = export_dataset_to_csv_bytes(
            session,
            dataset_id=dataset.id,
            include_marawa_metadata=True,
        )
        compressed_bytes = gzip.compress(csv_bytes, mtime=0)
        checksum = hashlib.sha256(compressed_bytes).hexdigest()

        self.storage.put(snapshot.object_key, compressed_bytes)
        snapshot.status = DatasetSnapshotStatus.READY
        snapshot.content_sha256 = checksum
        snapshot.content_length = len(compressed_bytes)

        # A newly created checkpoint must never be immediately evicted when a
        # recovery is temporarily protecting an older requested snapshot.
        protected = {*protect_snapshot_ids, snapshot.id}
        self._enforce_retention(session, protected_snapshot_ids=protected)
        return snapshot

    def list_snapshots(self, session: Session, *, limit: int = 100) -> list[DatasetSnapshot]:
        return list(
            session.scalars(
                select(DatasetSnapshot)
                .order_by(DatasetSnapshot.sequence.desc())
                .limit(limit)
            ).all()
        )

    def get_snapshot(self, session: Session, snapshot_id: str) -> DatasetSnapshot:
        snapshot = session.get(DatasetSnapshot, snapshot_id)
        if snapshot is None:
            raise DatasetSnapshotNotFoundError("Time Machine snapshot not found.")
        return snapshot

    def current_difference(self, session: Session, snapshot: DatasetSnapshot) -> dict[str, Any]:
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        if active_dataset is None:
            return {
                "current_dataset_version": None,
                "story_count_delta": None,
                "trope_count_delta": None,
                "theme_count_delta": None,
                "keyword_count_delta": None,
                "changes": None,
            }
        counts = _snapshot_counts(session, active_dataset.id)
        comparison_values = self._comparison_values_from_snapshot(snapshot)
        current_values = _current_dataset_comparison_values(session, active_dataset.id)
        return {
            "current_dataset_version": active_dataset.version,
            "story_count_delta": counts["story_count"] - snapshot.story_count,
            "trope_count_delta": counts["trope_count"] - snapshot.trope_count,
            "theme_count_delta": counts["theme_count"] - snapshot.theme_count,
            "keyword_count_delta": counts["keyword_count"] - snapshot.keyword_count,
            "changes": {
                "stories": _named_difference(comparison_values.story_titles, current_values.story_titles),
                "tropes": _named_difference(comparison_values.tropes, current_values.tropes),
                "themes": _named_difference(comparison_values.themes, current_values.themes),
                "keywords": _named_difference(comparison_values.keywords, current_values.keywords),
            },
        }

    def request_restore(
        self,
        session: Session,
        *,
        snapshot_id: str,
        actor_user_id: str,
    ) -> tuple[DatasetSnapshot, DatasetSnapshot, Job]:
        snapshot = self.get_snapshot(session, snapshot_id)
        if snapshot.status != DatasetSnapshotStatus.READY:
            raise DatasetSnapshotRestoreUnavailableError("This snapshot is not ready to recover.")

        active_job = session.scalar(
            select(Job)
            .where(
                Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
                Job.job_type.in_(("full_rebuild", "restore_snapshot")),
            )
            .order_by(Job.created_at.asc(), Job.id.asc())
        )
        if active_job is not None:
            raise DatasetSnapshotRestoreUnavailableError("Dataset maintenance is already in progress.")

        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        if active_dataset is None:
            raise DatasetSnapshotRestoreUnavailableError("There is no active dataset to safeguard before recovery.")

        safety_snapshot = self.capture(
            session,
            dataset=active_dataset,
            created_by_user_id=actor_user_id,
            reason="pre_restore",
            protect_snapshot_ids={snapshot.id},
        )
        # Keep both the requested archive and the just-created safety checkpoint
        # until the recovery job has read the requested archive.
        self._enforce_retention(
            session,
            protected_snapshot_ids={snapshot.id, safety_snapshot.id},
        )
        job = queue_job(
            session,
            job_type="restore_snapshot",
            payload={
                "snapshot_id": snapshot.id,
                "safety_snapshot_id": safety_snapshot.id,
                "actor_user_id": actor_user_id,
            },
        )
        record_audit_event(
            session,
            event_type="dataset.snapshot_restore_requested",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="dataset_snapshots",
            subject_id=snapshot.id,
            payload={
                "snapshot_sequence": snapshot.sequence,
                "restore_job_id": job.id,
                "safety_snapshot_id": safety_snapshot.id,
            },
        )
        session.commit()
        session.refresh(snapshot)
        session.refresh(safety_snapshot)
        session.refresh(job)
        return snapshot, safety_snapshot, job

    def handle_restore_job(
        self,
        session: Session,
        job: Job,
        *,
        rebuild_dataset: Callable[[Session, Job], dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot_id = str((job.payload_json or {}).get("snapshot_id") or "")
        snapshot = self.get_snapshot(session, snapshot_id)
        if snapshot.status != DatasetSnapshotStatus.READY:
            raise DatasetSnapshotRestoreUnavailableError("The selected snapshot is no longer ready to recover.")

        compressed_bytes = self.storage.get(snapshot.object_key)
        if snapshot.content_sha256 and hashlib.sha256(compressed_bytes).hexdigest() != snapshot.content_sha256:
            raise DatasetSnapshotRestoreUnavailableError("The selected snapshot failed its integrity check.")
        try:
            csv_bytes = gzip.decompress(compressed_bytes)
        except OSError as exc:
            raise DatasetSnapshotRestoreUnavailableError("The selected snapshot archive is invalid.") from exc

        try:
            restored_dataset = import_csv_bytes(
                session,
                csv_bytes,
                source_filename=f"time-machine-snapshot-{snapshot.sequence}.csv",
            )
        except CSVImportValidationError as exc:
            raise DatasetSnapshotRestoreUnavailableError(
                "The selected snapshot cannot be read by this version of Marawa."
            ) from exc

        # ``import_csv_bytes`` commits the staged dataset. Persist the link now
        # so an interrupted recovery is visible and can be marked failed.
        job.dataset_id = restored_dataset.id
        session.commit()

        rebuild_result = rebuild_dataset(session, job)
        actor_user_id = (job.payload_json or {}).get("actor_user_id")
        record_audit_event(
            session,
            event_type="dataset.snapshot_restored",
            actor_user_id=actor_user_id if isinstance(actor_user_id, str) else None,
            dataset_id=restored_dataset.id,
            subject_table="dataset_snapshots",
            subject_id=snapshot.id,
            payload={
                "snapshot_sequence": snapshot.sequence,
                "restore_job_id": job.id,
                "restored_dataset_id": restored_dataset.id,
            },
        )
        return {
            **rebuild_result,
            "message": "Time Machine recovery completed.",
            "restored_snapshot_id": snapshot.id,
            "restored_snapshot_sequence": snapshot.sequence,
            "safety_snapshot_id": (job.payload_json or {}).get("safety_snapshot_id"),
        }

    def _object_key(self, snapshot: DatasetSnapshot) -> str:
        filename = f"snapshot-{snapshot.sequence:08d}-{snapshot.id}.csv.gz"
        return f"{self.bucket_prefix}/{filename}" if self.bucket_prefix else filename

    def _comparison_values_from_snapshot(self, snapshot: DatasetSnapshot) -> DatasetComparisonValues:
        if snapshot.status != DatasetSnapshotStatus.READY:
            raise DatasetSnapshotComparisonUnavailableError("This checkpoint is not ready to inspect.")
        try:
            compressed_bytes = self.storage.get(snapshot.object_key)
            if snapshot.content_sha256 and hashlib.sha256(compressed_bytes).hexdigest() != snapshot.content_sha256:
                raise DatasetSnapshotComparisonUnavailableError("The selected checkpoint failed its integrity check.")
            return extract_dataset_comparison_values(gzip.decompress(compressed_bytes))
        except DatasetSnapshotComparisonUnavailableError:
            raise
        except (SnapshotStorageError, OSError, CSVImportValidationError) as exc:
            raise DatasetSnapshotComparisonUnavailableError(
                "The selected checkpoint cannot be read for comparison."
            ) from exc

    def _enforce_retention(self, session: Session, *, protected_snapshot_ids: set[str]) -> None:
        # The application's sessions deliberately disable autoflush, so make
        # the newly ready checkpoint visible to the FIFO query explicitly.
        session.flush()
        ready_snapshots = list(
            session.scalars(
                select(DatasetSnapshot)
                .where(DatasetSnapshot.status == DatasetSnapshotStatus.READY)
                .order_by(DatasetSnapshot.sequence.asc())
            ).all()
        )
        excess = len(ready_snapshots) - self.retention_count
        if excess <= 0:
            return

        candidates = [snapshot for snapshot in ready_snapshots if snapshot.id not in protected_snapshot_ids]
        for snapshot in candidates[:excess]:
            snapshot.status = DatasetSnapshotStatus.DELETING
            self.storage.delete(snapshot.object_key)
            session.delete(snapshot)
        session.flush()
