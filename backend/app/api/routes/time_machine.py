from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import DatasetSnapshot, UserRole
from app.services.auth import AuthSessionContext
from app.services.snapshots import (
    DatasetSnapshotComparisonUnavailableError,
    DatasetSnapshotNotFoundError,
    DatasetSnapshotRestoreUnavailableError,
)


class SnapshotCountsResponse(BaseModel):
    stories: int
    tropes: int
    themes: int
    keywords: int


class SnapshotDifferenceItemResponse(BaseModel):
    text: str
    count: int


class SnapshotValueDifferenceResponse(BaseModel):
    current_only: list[SnapshotDifferenceItemResponse]
    checkpoint_only: list[SnapshotDifferenceItemResponse]


class SnapshotContentDifferenceResponse(BaseModel):
    stories: SnapshotValueDifferenceResponse
    tropes: SnapshotValueDifferenceResponse
    themes: SnapshotValueDifferenceResponse
    keywords: SnapshotValueDifferenceResponse


class SnapshotDifferenceResponse(BaseModel):
    current_dataset_version: int | None
    story_count_delta: int | None
    trope_count_delta: int | None
    theme_count_delta: int | None
    keyword_count_delta: int | None
    changes: SnapshotContentDifferenceResponse | None


class TimeMachineSnapshotResponse(BaseModel):
    id: str
    sequence: int
    status: str
    reason: str
    source_job_id: str | None
    source_dataset_version: int | None
    source_filename: str | None
    created_at: str
    content_length: int | None
    counts: SnapshotCountsResponse
    difference_from_current: SnapshotDifferenceResponse | None = None


class RestoreSnapshotResponse(BaseModel):
    snapshot: TimeMachineSnapshotResponse
    safety_snapshot: TimeMachineSnapshotResponse
    job_id: str
    job_status: str


def _serialize_snapshot(snapshot: DatasetSnapshot, *, difference: dict | None = None) -> TimeMachineSnapshotResponse:
    return TimeMachineSnapshotResponse(
        id=snapshot.id,
        sequence=snapshot.sequence,
        status=snapshot.status.value,
        reason=snapshot.reason,
        source_job_id=snapshot.source_job_id,
        source_dataset_version=snapshot.source_dataset_version,
        source_filename=snapshot.source_filename,
        created_at=snapshot.created_at.isoformat(),
        content_length=snapshot.content_length,
        counts=SnapshotCountsResponse(
            stories=snapshot.story_count,
            tropes=snapshot.trope_count,
            themes=snapshot.theme_count,
            keywords=snapshot.keyword_count,
        ),
        difference_from_current=None if difference is None else SnapshotDifferenceResponse(**difference),
    )


router = APIRouter(prefix="/time-machine", tags=["time-machine"])


@router.get("", response_model=list[TimeMachineSnapshotResponse])
def list_time_machine_snapshots(
    request: Request,
    _: AuthSessionContext = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[TimeMachineSnapshotResponse]:
    service = request.app.state.snapshot_service
    return [_serialize_snapshot(snapshot) for snapshot in service.list_snapshots(session)]


@router.get("/{snapshot_id}", response_model=TimeMachineSnapshotResponse)
def read_time_machine_snapshot(
    snapshot_id: str,
    request: Request,
    _: AuthSessionContext = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> TimeMachineSnapshotResponse:
    service = request.app.state.snapshot_service
    try:
        snapshot = service.get_snapshot(session, snapshot_id)
    except DatasetSnapshotNotFoundError as exc:
        raise api_error(404, "snapshot_not_found", str(exc)) from exc
    try:
        difference = service.current_difference(session, snapshot)
    except DatasetSnapshotComparisonUnavailableError as exc:
        raise api_error(409, "snapshot_comparison_unavailable", str(exc)) from exc
    return _serialize_snapshot(snapshot, difference=difference)


@router.post("/{snapshot_id}/restore", response_model=RestoreSnapshotResponse, status_code=202)
def restore_time_machine_snapshot(
    snapshot_id: str,
    request: Request,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> RestoreSnapshotResponse:
    service = request.app.state.snapshot_service
    try:
        snapshot, safety_snapshot, job = service.request_restore(
            session,
            snapshot_id=snapshot_id,
            actor_user_id=auth_context.user.id,
        )
    except DatasetSnapshotNotFoundError as exc:
        raise api_error(404, "snapshot_not_found", str(exc)) from exc
    except DatasetSnapshotRestoreUnavailableError as exc:
        raise api_error(409, "snapshot_restore_unavailable", str(exc)) from exc
    return RestoreSnapshotResponse(
        snapshot=_serialize_snapshot(snapshot),
        safety_snapshot=_serialize_snapshot(safety_snapshot),
        job_id=job.id,
        job_status=job.status.value,
    )
