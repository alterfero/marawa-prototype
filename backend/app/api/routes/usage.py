from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_auth_context_optional, get_db_session, require_minimum_role
from app.api.errors import api_error
from app.core.config import get_settings
from app.db.models import UserRole
from app.services.auth import AuthSessionContext
from app.services.usage import (
    USAGE_COOKIE_MAX_AGE,
    USAGE_COOKIE_NAME,
    browser_identity,
    export_usage_csv,
    get_usage_report,
    record_activity,
    validate_range,
)


class UsageUserResponse(BaseModel):
    id: str
    display_name: str
    email: str


class UsageHourResponse(BaseModel):
    hour_start: str
    total_visitors: int
    registered_users: list[UsageUserResponse]
    unregistered_guests: int


class UsageResponse(BaseModel):
    start: str
    end: str
    first_recorded_at: str | None
    generated_at: str
    hours: list[UsageHourResponse]


router = APIRouter(tags=["usage"])


@router.post("/usage/activity", status_code=204)
def record_browser_activity(
    request: Request,
    session: Session = Depends(get_db_session),
    auth: AuthSessionContext | None = Depends(get_current_auth_context_optional),
) -> Response:
    # A custom header forces cross-origin browsers through our CORS allowlist.
    if request.headers.get("X-Usage-Activity") != "1":
        raise api_error(403, "usage_header_required", "A browser activity header is required.")
    settings = get_settings()
    cookie, browser_key = browser_identity(settings, request.cookies.get(USAGE_COOKIE_NAME))
    record_activity(session, browser_key=browser_key, user=auth.user if auth else None)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    if cookie != request.cookies.get(USAGE_COOKIE_NAME):
        response.set_cookie(
            USAGE_COOKIE_NAME, cookie, max_age=USAGE_COOKIE_MAX_AGE, httponly=True,
            secure=settings.session_cookie_secure, samesite=settings.session_cookie_same_site, path="/",
        )
    return response


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    try:
        return validate_range(start, end)
    except ValueError as exc:
        raise api_error(422, "usage_range_invalid", str(exc)) from exc


@router.get("/admin/usage", response_model=UsageResponse, dependencies=[Depends(require_minimum_role(UserRole.ADMIN))])
def read_usage(
    response: Response,
    start: datetime | None = None,
    end: datetime | None = None,
    session: Session = Depends(get_db_session),
) -> dict:
    start, end = _range(start, end)
    response.headers["Cache-Control"] = "no-store"
    return get_usage_report(session, start, end)


@router.get("/admin/usage/export.csv", dependencies=[Depends(require_minimum_role(UserRole.ADMIN))])
def download_usage_csv(request: Request, start: datetime | None = None, end: datetime | None = None) -> StreamingResponse:
    if start is not None or end is not None:
        start, end = _range(start, end)
    return StreamingResponse(
        export_usage_csv(request.app.state.session_factory, start, end),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="marawa-usage-utc.csv"', "Cache-Control": "no-store"},
    )
