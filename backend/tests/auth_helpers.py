from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models import UserRole


def configure_auth_env(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "supersecret-admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Bootstrap Admin")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()


def login_admin(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "supersecret-admin"},
    )
    assert response.status_code == 200
    return response.json()


def set_csrf_header(client: TestClient, csrf_token: str) -> None:
    client.headers.update({"X-CSRF-Token": csrf_token})


def authenticate_admin(client: TestClient) -> dict:
    session = login_admin(client)
    set_csrf_header(client, session["csrf_token"])
    return session


def create_invited_user(
    admin_client: TestClient,
    *,
    email: str,
    display_name: str,
    role: UserRole,
) -> dict:
    response = admin_client.post(
        "/api/admin/users",
        json={
            "email": email,
            "display_name": display_name,
            "role": role.value,
        },
    )
    assert response.status_code == 201
    return response.json()


def redeem_token(client: TestClient, *, token: str, new_password: str) -> dict:
    response = client.post(
        "/api/auth/redeem-token",
        json={
            "token": token,
            "new_password": new_password,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    set_csrf_header(client, payload["csrf_token"])
    return payload


def authenticate_role(
    admin_client: TestClient,
    user_client: TestClient,
    *,
    email: str,
    display_name: str,
    role: UserRole,
    password: str,
) -> dict:
    invited_user = create_invited_user(
        admin_client,
        email=email,
        display_name=display_name,
        role=role,
    )
    return redeem_token(user_client, token=invited_user["invite_token"], new_password=password)
