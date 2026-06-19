from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_auth_context, get_db_session, require_csrf
from app.api.errors import api_error
from app.core.config import get_settings
from app.services.auth import (
    AuthSessionContext,
    InvalidCredentialsError,
    PasswordValidationError,
    TokenRedemptionError,
    authenticate_user,
    ensure_utc,
    redeem_invite_or_reset_token,
    revoke_session,
    serialize_user,
)


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    status: str
    last_login_at: str | None = None
    deactivated_at: str | None = None
    created_at: str
    updated_at: str


class AuthSessionResponse(BaseModel):
    user: CurrentUserResponse
    csrf_token: str
    expires_at: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class RedeemTokenRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    display_name: str | None = None


class LogoutResponse(BaseModel):
    ok: bool


router = APIRouter(prefix="/auth", tags=["auth"])


def _request_ip_address(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or None
    if request.client is None:
        return None
    return request.client.host


def _set_auth_cookies(response: Response, *, session_token: str, csrf_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_same_site,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_same_site,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _serialize_auth_result(result) -> AuthSessionResponse:
    return AuthSessionResponse(
        user=CurrentUserResponse(**serialize_user(result.user)),
        csrf_token=result.csrf_token,
        expires_at=ensure_utc(result.user_session.expires_at).isoformat(),
    )


@router.post("/login", response_model=AuthSessionResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
) -> AuthSessionResponse:
    try:
        auth_result = authenticate_user(
            session,
            get_settings(),
            email=payload.email,
            password=payload.password,
            ip_address=_request_ip_address(request),
            user_agent=request.headers.get("user-agent"),
        )
    except InvalidCredentialsError as exc:
        raise api_error(401, "invalid_credentials", str(exc)) from exc

    session_response = _serialize_auth_result(auth_result)
    _set_auth_cookies(
        response,
        session_token=auth_result.session_token,
        csrf_token=auth_result.csrf_token,
    )
    return session_response


@router.post("/redeem-token", response_model=AuthSessionResponse)
def redeem_token(
    payload: RedeemTokenRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
) -> AuthSessionResponse:
    try:
        auth_result = redeem_invite_or_reset_token(
            session,
            get_settings(),
            token=payload.token,
            new_password=payload.new_password,
            display_name=payload.display_name,
            ip_address=_request_ip_address(request),
            user_agent=request.headers.get("user-agent"),
        )
    except PasswordValidationError as exc:
        raise api_error(400, "password_invalid", str(exc)) from exc
    except TokenRedemptionError as exc:
        raise api_error(400, "token_redemption_invalid", str(exc)) from exc

    session_response = _serialize_auth_result(auth_result)
    _set_auth_cookies(
        response,
        session_token=auth_result.session_token,
        csrf_token=auth_result.csrf_token,
    )
    return session_response


@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    session: Session = Depends(get_db_session),
    auth_context: AuthSessionContext = Depends(require_csrf),
) -> LogoutResponse:
    revoke_session(session, auth_context=auth_context)
    _clear_auth_cookies(response)
    return LogoutResponse(ok=True)


@router.get("/me", response_model=CurrentUserResponse)
def read_current_user(
    auth_context: AuthSessionContext = Depends(get_current_auth_context),
) -> CurrentUserResponse:
    return CurrentUserResponse(**serialize_user(auth_context.user))
