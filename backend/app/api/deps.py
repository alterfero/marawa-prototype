from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.core.config import get_settings
from app.db.models import UserRole
from app.services.auth import (
    AuthSessionContext,
    AuthorizationError,
    CsrfValidationError,
    get_auth_context_from_session_token,
    verify_csrf_token,
)
from app.services.maintenance import get_dataset_maintenance_status


ROLE_LEVELS = {
    UserRole.GUEST: 1,
    UserRole.CONTRIBUTOR: 2,
    UserRole.ADMIN: 3,
}


def get_db_session(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def get_current_auth_context_optional(
    request: Request,
    session: Session = Depends(get_db_session),
) -> AuthSessionContext | None:
    settings = get_settings()
    return get_auth_context_from_session_token(
        session,
        settings,
        session_token=request.cookies.get(settings.session_cookie_name),
    )


def get_current_auth_context(
    auth_context: AuthSessionContext | None = Depends(get_current_auth_context_optional),
) -> AuthSessionContext:
    if auth_context is None:
        raise api_error(401, "authentication_required", "Authentication is required.")
    return auth_context


def get_current_user(auth_context: AuthSessionContext = Depends(get_current_auth_context)):
    return auth_context.user


def require_minimum_role(minimum_role: UserRole):
    def dependency(auth_context: AuthSessionContext = Depends(get_current_auth_context)) -> AuthSessionContext:
        current_level = ROLE_LEVELS.get(auth_context.user.role, 0)
        required_level = ROLE_LEVELS.get(minimum_role, 0)
        if current_level < required_level:
            raise api_error(403, "forbidden", "You do not have access to this resource.")
        return auth_context

    return dependency


def require_csrf(
    request: Request,
    auth_context: AuthSessionContext = Depends(get_current_auth_context),
) -> AuthSessionContext:
    settings = get_settings()
    try:
        verify_csrf_token(
            settings,
            auth_context=auth_context,
            csrf_token=request.headers.get("X-CSRF-Token"),
        )
    except CsrfValidationError as exc:
        raise api_error(403, "csrf_invalid", str(exc)) from exc
    return auth_context


def require_minimum_role_with_csrf(minimum_role: UserRole):
    def dependency(
        request: Request,
        auth_context: AuthSessionContext = Depends(require_minimum_role(minimum_role)),
    ) -> AuthSessionContext:
        settings = get_settings()
        try:
            verify_csrf_token(
                settings,
                auth_context=auth_context,
                csrf_token=request.headers.get("X-CSRF-Token"),
            )
        except CsrfValidationError as exc:
            raise api_error(403, "csrf_invalid", str(exc)) from exc
        return auth_context

    return dependency


_DATASET_MUTATION_PREFIXES = (
    "/dataset",
    "/stories",
    "/tropes",
    "/keywords",
    "/themes",
    "/curation",
    "/review",
    "/time-machine",
)


def enforce_dataset_maintenance_lock(
    request: Request,
    session: Session = Depends(get_db_session),
) -> None:
    """Block dataset-scoped writes while replacement or rebuild work is active.

    This is intentionally an API-level dependency so it covers every browser
    client and direct API caller, rather than relying on disabled UI controls.
    """

    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return

    api_prefix = get_settings().api_prefix.rstrip("/")
    path = request.url.path
    relative_path = path[len(api_prefix) :] if path.startswith(api_prefix) else path
    if not relative_path.startswith(_DATASET_MUTATION_PREFIXES):
        return

    maintenance = get_dataset_maintenance_status(session)
    if not maintenance["active"]:
        return

    # A staged replacement has to be allowed to start its first rebuild. Once
    # a rebuild is queued or running, subsequent rebuild requests are blocked.
    if relative_path == "/dataset/rebuild" and maintenance["state"] == "staged":
        return

    raise api_error(
        409,
        "dataset_maintenance_in_progress",
        maintenance["message"],
        {"maintenance": maintenance},
    )
