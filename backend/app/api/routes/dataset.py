from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.core.config import get_settings
from app.db import Dataset, DatasetStatus
from app.db.models import UserRole
from app.services.audit import record_audit_event
from app.services.auth import AuthSessionContext
from app.services.csv_io import CSVImportValidationError, export_active_dataset_to_csv_bytes
from app.services.dataset import clear_dataset_data, get_dataset_status, upload_dataset_csv


class JobSummaryResponse(BaseModel):
    id: str
    status: str
    job_type: str


class EmbeddingStatusResponse(BaseModel):
    state: str
    ready: bool
    current: bool
    model_name: str
    artifact_version: int | None
    rebuilt_dataset_version: int | None
    indexed_trope_count: int
    indexed_keyword_count: int
    last_built_at: str | None
    last_error_message: str | None
    latest_rebuild_job: JobSummaryResponse | None


class DatasetStatusResponse(BaseModel):
    story_count: int
    trope_count: int
    keyword_count: int
    active_dataset_version: int | None
    latest_job: JobSummaryResponse | None
    embedding_status: EmbeddingStatusResponse


class DatasetUploadResponse(BaseModel):
    dataset_id: str
    dataset_version: int
    dataset_status: str
    active_dataset_version: int | None
    latest_job: JobSummaryResponse


router = APIRouter(prefix="/dataset", tags=["dataset"])


async def _read_upload_bytes(file: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total_size = 0
    while chunk := await file.read(1024 * 1024):
        total_size += len(chunk)
        if total_size > max_bytes:
            raise api_error(
                413,
                "file_too_large",
                f"The uploaded CSV exceeds the {max_bytes} byte limit.",
                {"max_upload_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/status", response_model=DatasetStatusResponse)
def read_dataset_status(
    request: Request,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> DatasetStatusResponse:
    return DatasetStatusResponse(
        **get_dataset_status(session, model_name=request.app.state.search_service.model_name)
    )


@router.post("/upload", response_model=DatasetUploadResponse, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> DatasetUploadResponse:
    settings = get_settings()
    try:
        csv_bytes = await _read_upload_bytes(file, max_bytes=settings.max_upload_bytes)
        dataset, job = upload_dataset_csv(
            session,
            csv_bytes,
            source_filename=file.filename,
            actor_user_id=auth_context.user.id,
        )
    except CSVImportValidationError as exc:
        raise api_error(400, "csv_import_invalid", str(exc)) from exc

    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))

    return DatasetUploadResponse(
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        dataset_status=dataset.status.value,
        active_dataset_version=active_dataset.version if active_dataset is not None else None,
        latest_job=JobSummaryResponse(id=job.id, status=job.status.value, job_type=job.job_type),
    )


@router.delete("", response_model=DatasetStatusResponse)
def clear_dataset(
    request: Request,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> DatasetStatusResponse:
    clear_dataset_data(session, actor_user_id=auth_context.user.id)
    return DatasetStatusResponse(
        **get_dataset_status(session, model_name=request.app.state.search_service.model_name)
    )


@router.get("/export.csv")
def export_dataset_csv(
    auth_context: AuthSessionContext = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> Response:
    try:
        csv_bytes = export_active_dataset_to_csv_bytes(session)
    except CSVImportValidationError as exc:
        raise api_error(404, "active_dataset_not_found", str(exc)) from exc

    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    record_audit_event(
        session,
        event_type="dataset.exported",
        actor_user_id=auth_context.user.id,
        dataset_id=active_dataset.id if active_dataset is not None else None,
        subject_table="datasets",
        subject_id=active_dataset.id if active_dataset is not None else None,
        payload={
            "dataset_version": active_dataset.version if active_dataset is not None else None,
            "content_length": len(csv_bytes),
        },
    )
    session.commit()

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dataset-export.csv"'},
    )
