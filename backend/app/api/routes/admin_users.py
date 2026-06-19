from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.core.config import get_settings
from app.db.models import UserRole
from app.services.auth import (
    AuthSessionContext,
    UserAlreadyExistsError,
    UserLifecycleError,
    UserMutationConflictError,
    UserNotFoundError,
    activate_user,
    create_invited_user,
    ensure_utc,
    issue_admin_reset_token,
    list_users,
    serialize_user,
    update_user,
    deactivate_user,
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


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=3)
    display_name: str = Field(min_length=1)
    role: UserRole = UserRole.GUEST


class CreateUserResponse(BaseModel):
    user: CurrentUserResponse
    invite_token: str
    token_kind: str
    expires_at: str


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    role: UserRole | None = None


class PasswordResetResponse(BaseModel):
    user: CurrentUserResponse
    reset_token: str
    token_kind: str
    expires_at: str


class UserLifecycleResponse(BaseModel):
    user: CurrentUserResponse
    revoked_session_count: int | None = None


router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _serialize_user_response(user) -> CurrentUserResponse:
    return CurrentUserResponse(**serialize_user(user))


@router.get("", response_model=list[CurrentUserResponse])
def read_users(
    _: AuthSessionContext = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[CurrentUserResponse]:
    return [CurrentUserResponse(**user) for user in list_users(session)]


@router.post("", response_model=CreateUserResponse, status_code=201)
def create_user(
    payload: CreateUserRequest,
    session: Session = Depends(get_db_session),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
) -> CreateUserResponse:
    try:
        user, token = create_invited_user(
            session,
            settings=get_settings(),
            actor_user_id=auth_context.user.id,
            email=payload.email,
            display_name=payload.display_name,
            role=payload.role,
        )
    except UserAlreadyExistsError as exc:
        raise api_error(409, "user_exists", str(exc)) from exc
    except UserLifecycleError as exc:
        raise api_error(400, "user_invalid", str(exc)) from exc

    return CreateUserResponse(
        user=_serialize_user_response(user),
        invite_token=token.token,
        token_kind=token.token_kind.value,
        expires_at=ensure_utc(token.expires_at).isoformat(),
    )


@router.patch("/{user_id}", response_model=CurrentUserResponse)
def patch_user(
    user_id: str,
    payload: UpdateUserRequest,
    session: Session = Depends(get_db_session),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
) -> CurrentUserResponse:
    try:
        user = update_user(
            session,
            actor_user_id=auth_context.user.id,
            target_user_id=user_id,
            display_name=payload.display_name,
            role=payload.role,
        )
    except UserNotFoundError as exc:
        raise api_error(404, "user_not_found", str(exc)) from exc
    except UserMutationConflictError as exc:
        raise api_error(409, "user_mutation_conflict", str(exc)) from exc
    except UserLifecycleError as exc:
        raise api_error(400, "user_invalid", str(exc)) from exc

    return _serialize_user_response(user)


@router.post("/{user_id}/deactivate", response_model=UserLifecycleResponse)
def deactivate_user_account(
    user_id: str,
    session: Session = Depends(get_db_session),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
) -> UserLifecycleResponse:
    try:
        user = deactivate_user(
            session,
            actor_user_id=auth_context.user.id,
            target_user_id=user_id,
        )
    except UserNotFoundError as exc:
        raise api_error(404, "user_not_found", str(exc)) from exc
    except UserMutationConflictError as exc:
        raise api_error(409, "user_mutation_conflict", str(exc)) from exc
    except UserLifecycleError as exc:
        raise api_error(400, "user_invalid", str(exc)) from exc

    return UserLifecycleResponse(user=_serialize_user_response(user))


@router.post("/{user_id}/activate", response_model=UserLifecycleResponse)
def activate_user_account(
    user_id: str,
    session: Session = Depends(get_db_session),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
) -> UserLifecycleResponse:
    try:
        user = activate_user(session, target_user_id=user_id, actor_user_id=auth_context.user.id)
    except UserNotFoundError as exc:
        raise api_error(404, "user_not_found", str(exc)) from exc
    except UserLifecycleError as exc:
        raise api_error(400, "user_invalid", str(exc)) from exc

    return UserLifecycleResponse(user=_serialize_user_response(user))


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_user_password(
    user_id: str,
    session: Session = Depends(get_db_session),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
) -> PasswordResetResponse:
    try:
        user, token = issue_admin_reset_token(
            session,
            settings=get_settings(),
            actor_user_id=auth_context.user.id,
            target_user_id=user_id,
        )
    except UserNotFoundError as exc:
        raise api_error(404, "user_not_found", str(exc)) from exc
    except UserLifecycleError as exc:
        raise api_error(400, "user_invalid", str(exc)) from exc

    return PasswordResetResponse(
        user=_serialize_user_response(user),
        reset_token=token.token,
        token_kind=token.token_kind.value,
        expires_at=ensure_utc(token.expires_at).isoformat(),
    )
