from typing import Any, Literal

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.api.routes.dataset import _read_upload_bytes
from app.core.config import get_settings
from app.db.models import UserRole
from app.services.auth import AuthSessionContext
from app.services.injection import InjectionError, apply_injection, get_injection, preview_injection

router = APIRouter(prefix="/dataset/injections", tags=["dataset"])


class InjectionIssueResponse(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    suggestion: str
    row_number: int | None
    line_start: int | None
    line_end: int | None
    column: str | None
    column_number: int | None
    value: str | None


class InjectionCandidateResponse(BaseModel):
    key: str
    kind: Literal["database", "file"]
    story_id: str | None
    row_number: int | None
    version: int | None
    fields: dict[str, str]
    metadata: dict[str, Any] | None
    reasons: list[str]


class InjectionRowResponse(BaseModel):
    row_number: int
    line_start: int
    line_end: int
    fields: dict[str, str]
    metadata: dict[str, Any] | None
    candidates: list[InjectionCandidateResponse]


class InjectionResultResponse(BaseModel):
    id: str
    status: Literal["applied"]
    added: int
    updated: int
    skipped: int
    new_terms: int
    dataset_version: int
    job_id: str | None


class InjectionPreviewResponse(BaseModel):
    id: str
    format: Literal["legacy", "full"]
    source_filename: str | None
    dataset_version: int
    row_count: int
    error_count: int
    warning_count: int
    duplicate_count: int
    issues: list[InjectionIssueResponse]
    rows: list[InjectionRowResponse]
    result: InjectionResultResponse | None = None


class InjectionDecision(BaseModel):
    row_number: int = Field(ge=1)
    action: Literal["keep_both", "keep_original", "keep_injected"]
    target_key: str | None = None


class InjectionApplyRequest(BaseModel):
    decisions: list[InjectionDecision] = Field(default_factory=list)
    acknowledge_warnings: bool = False


@router.post("/preview", response_model=InjectionPreviewResponse)
async def preview_csv_injection(
    file: UploadFile = File(...),
    _: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
):
    content = await _read_upload_bytes(file, max_bytes=get_settings().max_upload_bytes)
    try:
        return preview_injection(session, content, source_filename=file.filename)
    except InjectionError as exc:
        raise api_error(exc.status_code, "injection_unavailable", str(exc)) from exc


@router.get("/{injection_id}", response_model=InjectionPreviewResponse)
def read_csv_injection(
    injection_id: str,
    _: AuthSessionContext = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
):
    try:
        return get_injection(session, injection_id)
    except InjectionError as exc:
        raise api_error(exc.status_code, "injection_unavailable", str(exc)) from exc


@router.post("/{injection_id}/apply", response_model=InjectionResultResponse)
def apply_csv_injection(
    injection_id: str,
    payload: InjectionApplyRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
):
    try:
        return apply_injection(session, injection_id, decisions=[d.model_dump() for d in payload.decisions],
                               acknowledge_warnings=payload.acknowledge_warnings, actor_user_id=auth_context.user.id)
    except InjectionError as exc:
        raise api_error(exc.status_code, "injection_invalid", str(exc)) from exc
