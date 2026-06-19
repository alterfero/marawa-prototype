from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.parsing import clean_text
from app.db.models import InviteResetToken, InviteResetTokenKind, User, UserRole, UserSession, UserStatus
from app.services.audit import record_audit_event


_password_hasher = PasswordHasher()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_email(email: str) -> str:
    return clean_text(email).lower()


def _hash_secret(secret: str, value: str) -> str:
    return hashlib.sha256(f"{secret}:{value}".encode("utf-8")).hexdigest()


def _generate_secret_token() -> str:
    return secrets.token_urlsafe(32)


def _session_expiry(settings: Settings) -> datetime:
    return utc_now() + timedelta(hours=settings.session_ttl_hours)


def _invite_reset_expiry(settings: Settings) -> datetime:
    return utc_now() + timedelta(hours=settings.invite_reset_ttl_hours)


@dataclass
class AuthSessionContext:
    user: User
    user_session: UserSession


@dataclass
class AuthSessionResult:
    user: User
    user_session: UserSession
    session_token: str
    csrf_token: str


@dataclass
class TokenIssueResult:
    token: str
    expires_at: datetime
    token_kind: InviteResetTokenKind


class AuthServiceError(ValueError):
    """Base auth and user lifecycle error."""


class InvalidCredentialsError(AuthServiceError):
    """Raised when login credentials are invalid."""


class AuthenticationRequiredError(AuthServiceError):
    """Raised when no valid authenticated session exists."""


class AuthorizationError(AuthServiceError):
    """Raised when the user lacks the required role."""


class CsrfValidationError(AuthServiceError):
    """Raised when the CSRF token is missing or invalid."""


class TokenRedemptionError(AuthServiceError):
    """Raised when an invite or reset token cannot be redeemed."""


class PasswordValidationError(AuthServiceError):
    """Raised when a password fails validation."""


class UserLifecycleError(AuthServiceError):
    """Raised when a user lifecycle mutation is invalid."""


class UserAlreadyExistsError(UserLifecycleError):
    """Raised when a user email is already present."""


class UserNotFoundError(UserLifecycleError):
    """Raised when a target user does not exist."""


class UserMutationConflictError(UserLifecycleError):
    """Raised when a user mutation would create an unsafe state."""


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "status": user.status.value,
        "last_login_at": ensure_utc(user.last_login_at).isoformat() if user.last_login_at else None,
        "deactivated_at": ensure_utc(user.deactivated_at).isoformat() if user.deactivated_at else None,
        "created_at": ensure_utc(user.created_at).isoformat(),
        "updated_at": ensure_utc(user.updated_at).isoformat(),
    }


def list_users(session: Session) -> list[dict]:
    users = session.scalars(select(User).order_by(User.created_at.asc(), User.id.asc())).all()
    return [serialize_user(user) for user in users]


def authenticate_user(
    session: Session,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthSessionResult:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or user.password_hash is None or user.status != UserStatus.ACTIVE:
        raise InvalidCredentialsError("Email or password is invalid.")
    if not verify_password(user.password_hash, password):
        raise InvalidCredentialsError("Email or password is invalid.")
    result = create_user_session(
        session,
        settings,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    record_audit_event(
        session,
        event_type="auth.login",
        actor_user_id=user.id,
        subject_table="users",
        subject_id=user.id,
        payload={"email": user.email},
    )
    session.commit()
    return result


def create_user_session(
    session: Session,
    settings: Settings,
    *,
    user: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthSessionResult:
    now = utc_now()
    session_token = _generate_secret_token()
    csrf_token = _generate_secret_token()
    user_session = UserSession(
        user_id=user.id,
        session_token_hash=_hash_secret(settings.session_secret, session_token),
        csrf_token_hash=_hash_secret(settings.session_secret, csrf_token),
        expires_at=_session_expiry(settings),
        last_seen_at=now,
        ip_address=clean_text(ip_address) or None,
        user_agent=clean_text(user_agent) or None,
    )
    user.last_login_at = now
    session.add(user_session)
    session.commit()
    session.refresh(user)
    session.refresh(user_session)
    return AuthSessionResult(
        user=user,
        user_session=user_session,
        session_token=session_token,
        csrf_token=csrf_token,
    )


def get_auth_context_from_session_token(
    session: Session,
    settings: Settings,
    *,
    session_token: str | None,
) -> AuthSessionContext | None:
    cleaned_token = clean_text(session_token)
    if not cleaned_token:
        return None

    user_session = session.scalar(
        select(UserSession)
        .where(UserSession.session_token_hash == _hash_secret(settings.session_secret, cleaned_token))
    )
    if user_session is None:
        return None

    user = session.get(User, user_session.user_id)
    now = utc_now()
    expires_at = ensure_utc(user_session.expires_at)
    if (
        user is None
        or user.status != UserStatus.ACTIVE
        or user_session.revoked_at is not None
        or expires_at is None
        or expires_at <= now
    ):
        if user_session.revoked_at is None:
            user_session.revoked_at = now
            session.commit()
        return None

    return AuthSessionContext(user=user, user_session=user_session)


def verify_csrf_token(
    settings: Settings,
    *,
    auth_context: AuthSessionContext,
    csrf_token: str | None,
) -> None:
    cleaned_token = clean_text(csrf_token)
    if not cleaned_token:
        raise CsrfValidationError("A valid CSRF token is required.")

    expected_hash = _hash_secret(settings.session_secret, cleaned_token)
    if not secrets.compare_digest(expected_hash, auth_context.user_session.csrf_token_hash):
        raise CsrfValidationError("A valid CSRF token is required.")


def revoke_session(session: Session, *, auth_context: AuthSessionContext) -> None:
    if auth_context.user_session.revoked_at is None:
        auth_context.user_session.revoked_at = utc_now()
        record_audit_event(
            session,
            event_type="auth.logout",
            actor_user_id=auth_context.user.id,
            subject_table="users",
            subject_id=auth_context.user.id,
            payload={"session_id": auth_context.user_session.id},
        )
        session.commit()


def revoke_all_user_sessions(session: Session, *, user_id: str) -> int:
    now = utc_now()
    active_sessions = list(
        session.scalars(
            select(UserSession).where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
        ).all()
    )
    for user_session in active_sessions:
        user_session.revoked_at = now
    session.flush()
    return len(active_sessions)


def create_invited_user(
    session: Session,
    settings: Settings,
    *,
    actor_user_id: str,
    email: str,
    display_name: str,
    role: UserRole,
) -> tuple[User, TokenIssueResult]:
    normalized_email = normalize_email(email)
    if not normalized_email:
        raise UserLifecycleError("Email is required.")
    if session.scalar(select(User).where(User.email == normalized_email)) is not None:
        raise UserAlreadyExistsError("A user with this email already exists.")

    user = User(
        email=normalized_email,
        display_name=clean_text(display_name) or normalized_email,
        role=role,
        status=UserStatus.PENDING_INVITE,
    )
    session.add(user)
    session.flush()

    token = _issue_invite_or_reset_token(
        session,
        settings,
        user=user,
        created_by_user_id=actor_user_id,
        token_kind=InviteResetTokenKind.INVITE,
    )
    record_audit_event(
        session,
        event_type="user.created",
        actor_user_id=actor_user_id,
        subject_table="users",
        subject_id=user.id,
        payload={"email": user.email, "role": user.role.value, "status": user.status.value},
    )
    session.commit()
    session.refresh(user)
    return user, token


def redeem_invite_or_reset_token(
    session: Session,
    settings: Settings,
    *,
    token: str,
    new_password: str,
    display_name: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthSessionResult:
    validate_password(new_password)
    token_record = session.scalar(
        select(InviteResetToken).where(
            InviteResetToken.token_hash == _hash_secret(settings.session_secret, clean_text(token))
        )
    )
    now = utc_now()
    expires_at = ensure_utc(token_record.expires_at) if token_record is not None else None
    if (
        token_record is None
        or token_record.consumed_at is not None
        or expires_at is None
        or expires_at <= now
    ):
        raise TokenRedemptionError("The invite or reset token is invalid or expired.")

    user = session.get(User, token_record.user_id)
    if user is None:
        raise TokenRedemptionError("The invite or reset token is invalid or expired.")
    if user.status == UserStatus.INACTIVE:
        raise TokenRedemptionError("The user account is inactive and cannot redeem this token.")

    if token_record.token_kind == InviteResetTokenKind.ADMIN_RESET:
        revoke_all_user_sessions(session, user_id=user.id)
    _consume_outstanding_tokens(
        session,
        user_id=user.id,
        token_kind=token_record.token_kind,
        consumed_at=now,
    )

    user.password_hash = hash_password(new_password)
    user.status = UserStatus.ACTIVE
    user.deactivated_at = None
    normalized_display_name = clean_text(display_name or "")
    if normalized_display_name:
        user.display_name = normalized_display_name
    session.flush()
    result = create_user_session(
        session,
        settings,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    record_audit_event(
        session,
        event_type="auth.invite_redeemed" if token_record.token_kind == InviteResetTokenKind.INVITE else "auth.password_reset_redeemed",
        actor_user_id=user.id,
        subject_table="users",
        subject_id=user.id,
        payload={"token_kind": token_record.token_kind.value},
    )
    session.commit()
    return result


def update_user(
    session: Session,
    *,
    actor_user_id: str,
    target_user_id: str,
    display_name: str | None = None,
    role: UserRole | None = None,
) -> User:
    user = session.get(User, target_user_id)
    if user is None:
        raise UserNotFoundError("User not found.")
    if actor_user_id == target_user_id and role is not None and role != UserRole.ADMIN:
        raise UserMutationConflictError("Admins cannot remove their own admin role.")

    if display_name is not None:
        normalized_display_name = clean_text(display_name)
        if not normalized_display_name:
            raise UserLifecycleError("Display name cannot be empty.")
        user.display_name = normalized_display_name
    if role is not None:
        user.role = role

    record_audit_event(
        session,
        event_type="user.updated",
        actor_user_id=actor_user_id,
        subject_table="users",
        subject_id=user.id,
        payload={"display_name": user.display_name, "role": user.role.value},
    )
    session.commit()
    session.refresh(user)
    return user


def deactivate_user(
    session: Session,
    *,
    actor_user_id: str,
    target_user_id: str,
) -> User:
    if actor_user_id == target_user_id:
        raise UserMutationConflictError("Admins cannot deactivate their own account.")
    user = session.get(User, target_user_id)
    if user is None:
        raise UserNotFoundError("User not found.")
    user.status = UserStatus.INACTIVE
    user.deactivated_at = utc_now()
    revoke_all_user_sessions(session, user_id=user.id)
    record_audit_event(
        session,
        event_type="user.deactivated",
        actor_user_id=actor_user_id,
        subject_table="users",
        subject_id=user.id,
        payload={"status": user.status.value},
    )
    session.commit()
    session.refresh(user)
    return user


def activate_user(session: Session, *, target_user_id: str, actor_user_id: str | None = None) -> User:
    user = session.get(User, target_user_id)
    if user is None:
        raise UserNotFoundError("User not found.")
    user.deactivated_at = None
    user.status = UserStatus.ACTIVE if user.password_hash else UserStatus.PENDING_INVITE
    record_audit_event(
        session,
        event_type="user.activated",
        actor_user_id=actor_user_id,
        subject_table="users",
        subject_id=user.id,
        payload={"status": user.status.value},
    )
    session.commit()
    session.refresh(user)
    return user


def issue_admin_reset_token(
    session: Session,
    settings: Settings,
    *,
    actor_user_id: str,
    target_user_id: str,
) -> tuple[User, TokenIssueResult]:
    user = session.get(User, target_user_id)
    if user is None:
        raise UserNotFoundError("User not found.")
    if user.status == UserStatus.INACTIVE:
        raise UserLifecycleError("Inactive users must be reactivated before resetting their password.")

    token = _issue_invite_or_reset_token(
        session,
        settings,
        user=user,
        created_by_user_id=actor_user_id,
        token_kind=InviteResetTokenKind.ADMIN_RESET,
    )
    record_audit_event(
        session,
        event_type="user.password_reset_issued",
        actor_user_id=actor_user_id,
        subject_table="users",
        subject_id=user.id,
        payload={"token_kind": token.token_kind.value},
    )
    session.commit()
    session.refresh(user)
    return user, token


def ensure_bootstrap_admin(session: Session, settings: Settings) -> User | None:
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return None

    normalized_email = normalize_email(settings.bootstrap_admin_email)
    user = session.scalar(select(User).where(User.email == normalized_email))
    changed = False
    if user is None:
        user = User(
            email=normalized_email,
            display_name=clean_text(settings.bootstrap_admin_display_name) or normalized_email,
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            password_hash=hash_password(settings.bootstrap_admin_password),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    if user.role != UserRole.ADMIN:
        user.role = UserRole.ADMIN
        changed = True
    if user.status != UserStatus.ACTIVE:
        user.status = UserStatus.ACTIVE
        user.deactivated_at = None
        changed = True
    if user.password_hash is None:
        user.password_hash = hash_password(settings.bootstrap_admin_password)
        changed = True
    if not clean_text(user.display_name):
        user.display_name = clean_text(settings.bootstrap_admin_display_name) or normalized_email
        changed = True
    if changed:
        session.commit()
        session.refresh(user)
    return user


def hash_password(password: str) -> str:
    validate_password(password)
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise PasswordValidationError("Passwords must be at least 8 characters long.")


def _issue_invite_or_reset_token(
    session: Session,
    settings: Settings,
    *,
    user: User,
    created_by_user_id: str,
    token_kind: InviteResetTokenKind,
) -> TokenIssueResult:
    _consume_outstanding_tokens(session, user_id=user.id, token_kind=token_kind, consumed_at=utc_now())

    raw_token = _generate_secret_token()
    token_record = InviteResetToken(
        user_id=user.id,
        token_hash=_hash_secret(settings.session_secret, raw_token),
        token_kind=token_kind,
        created_by_user_id=created_by_user_id,
        expires_at=_invite_reset_expiry(settings),
    )
    session.add(token_record)
    session.flush()
    return TokenIssueResult(
        token=raw_token,
        expires_at=token_record.expires_at,
        token_kind=token_kind,
    )


def _consume_outstanding_tokens(
    session: Session,
    *,
    user_id: str,
    token_kind: InviteResetTokenKind,
    consumed_at: datetime,
) -> None:
    outstanding = list(
        session.scalars(
            select(InviteResetToken).where(
                InviteResetToken.user_id == user_id,
                InviteResetToken.token_kind == token_kind,
                InviteResetToken.consumed_at.is_(None),
            )
        ).all()
    )
    for token_record in outstanding:
        token_record.consumed_at = consumed_at
    session.flush()
