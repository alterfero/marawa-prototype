import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db import build_engine, build_session_factory
from app.main import create_app


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def configure_auth_env(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "supersecret-admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Bootstrap Admin")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()


def build_test_client(tmp_path, name: str) -> TestClient:
    db_path = tmp_path / name
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
    )
    return TestClient(app)


def login_admin(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "supersecret-admin"},
    )
    assert response.status_code == 200
    return response.json()


def test_bootstrap_admin_login_me_and_logout(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)

    with build_test_client(tmp_path, "auth-login.db") as client:
        login_response = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "supersecret-admin"},
        )

        assert login_response.status_code == 200
        body = login_response.json()
        assert body["user"]["email"] == "admin@example.com"
        assert body["user"]["role"] == "admin"
        assert body["csrf_token"]
        set_cookie_header = ",".join(login_response.headers.get_list("set-cookie"))
        assert "marawa_session=" in set_cookie_header
        assert "marawa_csrf=" in set_cookie_header

        me_response = client.get("/api/auth/me")
        assert me_response.status_code == 200
        assert me_response.json()["email"] == "admin@example.com"

        logout_without_csrf = client.post("/api/auth/logout")
        assert logout_without_csrf.status_code == 403
        assert logout_without_csrf.json()["code"] == "csrf_invalid"

        logout_response = client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": body["csrf_token"]},
        )
        assert logout_response.status_code == 200
        assert logout_response.json() == {"ok": True}

        me_after_logout = client.get("/api/auth/me")
        assert me_after_logout.status_code == 401
        assert me_after_logout.json()["code"] == "authentication_required"


def test_admin_can_create_invited_user_and_user_can_redeem_token(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)

    with build_test_client(tmp_path, "auth-invite.db") as admin_client:
        admin_session = login_admin(admin_client)

        forbidden_create = admin_client.post(
            "/api/admin/users",
            json={
                "email": "contributor@example.com",
                "display_name": "Contributor User",
                "role": "contributor",
            },
        )
        assert forbidden_create.status_code == 403
        assert forbidden_create.json()["code"] == "csrf_invalid"

        create_response = admin_client.post(
            "/api/admin/users",
            json={
                "email": "contributor@example.com",
                "display_name": "Contributor User",
                "role": "contributor",
            },
            headers={"X-CSRF-Token": admin_session["csrf_token"]},
        )
        assert create_response.status_code == 201
        create_body = create_response.json()
        assert create_body["user"]["status"] == "pending_invite"
        assert create_body["user"]["role"] == "contributor"
        assert create_body["token_kind"] == "invite"
        invite_token = create_body["invite_token"]

    with build_test_client(tmp_path, "auth-invite.db") as contributor_client:
        redeem_response = contributor_client.post(
            "/api/auth/redeem-token",
            json={
                "token": invite_token,
                "new_password": "new-contributor-password",
            },
        )
        assert redeem_response.status_code == 200
        redeem_body = redeem_response.json()
        assert redeem_body["user"]["email"] == "contributor@example.com"
        assert redeem_body["user"]["status"] == "active"
        assert redeem_body["user"]["role"] == "contributor"

        me_response = contributor_client.get("/api/auth/me")
        assert me_response.status_code == 200
        assert me_response.json()["email"] == "contributor@example.com"

        admin_only_response = contributor_client.get("/api/admin/users")
        assert admin_only_response.status_code == 403
        assert admin_only_response.json()["code"] == "forbidden"


def test_deactivation_revokes_sessions_and_activation_restores_login(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)

    with build_test_client(tmp_path, "auth-reactivate.db") as admin_client:
        admin_session = login_admin(admin_client)
        create_response = admin_client.post(
            "/api/admin/users",
            json={
                "email": "guest@example.com",
                "display_name": "Guest User",
                "role": "guest",
            },
            headers={"X-CSRF-Token": admin_session["csrf_token"]},
        )
        user_id = create_response.json()["user"]["id"]
        invite_token = create_response.json()["invite_token"]

    with build_test_client(tmp_path, "auth-reactivate.db") as guest_client:
        redeem_response = guest_client.post(
            "/api/auth/redeem-token",
            json={
                "token": invite_token,
                "new_password": "guest-password",
            },
        )
        assert redeem_response.status_code == 200
        guest_me = guest_client.get("/api/auth/me")
        assert guest_me.status_code == 200

        with build_test_client(tmp_path, "auth-reactivate.db") as admin_client:
            admin_session = login_admin(admin_client)
            deactivate_response = admin_client.post(
                f"/api/admin/users/{user_id}/deactivate",
                headers={"X-CSRF-Token": admin_session["csrf_token"]},
            )
            assert deactivate_response.status_code == 200
            assert deactivate_response.json()["user"]["status"] == "inactive"

            guest_after_deactivate = guest_client.get("/api/auth/me")
            assert guest_after_deactivate.status_code == 401

            activate_response = admin_client.post(
                f"/api/admin/users/{user_id}/activate",
                headers={"X-CSRF-Token": admin_session["csrf_token"]},
            )
            assert activate_response.status_code == 200
            assert activate_response.json()["user"]["status"] == "active"

    with build_test_client(tmp_path, "auth-reactivate.db") as reactivated_guest_client:
        relogin_response = reactivated_guest_client.post(
            "/api/auth/login",
            json={"email": "guest@example.com", "password": "guest-password"},
        )
        assert relogin_response.status_code == 200
        assert relogin_response.json()["user"]["status"] == "active"


def test_admin_reset_password_revokes_old_sessions_and_requires_new_password(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)

    with build_test_client(tmp_path, "auth-reset.db") as admin_client:
        admin_session = login_admin(admin_client)
        create_response = admin_client.post(
            "/api/admin/users",
            json={
                "email": "reader@example.com",
                "display_name": "Reader User",
                "role": "guest",
            },
            headers={"X-CSRF-Token": admin_session["csrf_token"]},
        )
        user_id = create_response.json()["user"]["id"]
        invite_token = create_response.json()["invite_token"]

    with build_test_client(tmp_path, "auth-reset.db") as user_client:
        redeem_response = user_client.post(
            "/api/auth/redeem-token",
            json={
                "token": invite_token,
                "new_password": "reader-password",
            },
        )
        assert redeem_response.status_code == 200
        assert user_client.get("/api/auth/me").status_code == 200

        with build_test_client(tmp_path, "auth-reset.db") as admin_client:
            admin_session = login_admin(admin_client)
            reset_response = admin_client.post(
                f"/api/admin/users/{user_id}/reset-password",
                headers={"X-CSRF-Token": admin_session["csrf_token"]},
            )
            assert reset_response.status_code == 200
            reset_token = reset_response.json()["reset_token"]

        reset_client = build_test_client(tmp_path, "auth-reset.db")
        with reset_client:
            redeem_reset = reset_client.post(
                "/api/auth/redeem-token",
                json={
                    "token": reset_token,
                    "new_password": "reader-password-updated",
                },
            )
            assert redeem_reset.status_code == 200
            assert redeem_reset.json()["user"]["email"] == "reader@example.com"

        old_session_me = user_client.get("/api/auth/me")
        assert old_session_me.status_code == 401

    with build_test_client(tmp_path, "auth-reset.db") as login_client:
        old_password_login = login_client.post(
            "/api/auth/login",
            json={"email": "reader@example.com", "password": "reader-password"},
        )
        assert old_password_login.status_code == 401

        new_password_login = login_client.post(
            "/api/auth/login",
            json={"email": "reader@example.com", "password": "reader-password-updated"},
        )
        assert new_password_login.status_code == 200
