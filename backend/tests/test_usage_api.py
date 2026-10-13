import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.db import build_engine, build_session_factory
from app.core.config import get_settings
from app.db.models import UsageActivity, User, UserRole, UserSession, UserStatus
from app.main import create_app
from app.services import usage
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env


HOUR = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
ACTIVITY_HEADERS = {"X-Usage-Activity": "1"}
RANGE = {"start": HOUR.isoformat(), "end": (HOUR + timedelta(hours=3)).isoformat()}


@pytest.fixture
def usage_app(monkeypatch, tmp_path):
    configure_auth_env(monkeypatch)
    monkeypatch.setattr(usage, "utc_now", lambda: HOUR + timedelta(minutes=20))
    engine = build_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    app = create_app(db_engine=engine, session_factory=build_session_factory(engine), job_runner_enabled=False)
    with TestClient(app) as client:
        yield app, client
    engine.dispose()


def report(client, **params):
    response = client.get("/api/admin/usage", params=params or RANGE)
    assert response.status_code == 200, response.text
    return response.json()


def test_guests_deduplicate_and_hours_are_utc_with_empty_buckets(usage_app, monkeypatch):
    app, admin = usage_app
    authenticate_admin(admin)
    with TestClient(app) as guest, TestClient(app) as other_guest:
        for _ in range(3):
            assert guest.post("/api/usage/activity", headers=ACTIVITY_HEADERS).status_code == 204
        assert other_guest.post("/api/usage/activity", headers=ACTIVITY_HEADERS).status_code == 204
        monkeypatch.setattr(usage, "utc_now", lambda: HOUR + timedelta(hours=1))
        guest.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
    data = report(admin)
    assert [hour["total_visitors"] for hour in data["hours"]] == [2, 1, 0]
    assert data["hours"][0]["registered_users"] == []
    assert data["hours"][0]["unregistered_guests"] == 2
    assert data["hours"][1]["hour_start"] == "2026-10-13T11:00:00+00:00"
    assert data["first_recorded_at"] == "2026-10-13T10:20:00+00:00"
    offset_range = {"start": "2026-10-13T00:00:00-10:00", "end": "2026-10-13T03:00:00-10:00"}
    assert report(admin, **offset_range)["hours"] == data["hours"]


def test_login_logout_and_multiple_devices_count_one_account(usage_app):
    app, client = usage_app
    client.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
    user = authenticate_admin(client)["user"]
    client.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
    with TestClient(app) as other_device:
        authenticate_admin(other_device)
        other_device.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
        client.post("/api/auth/logout")
        client.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
        hour = report(other_device)["hours"][0]
    assert hour["total_visitors"] == 1
    assert hour["unregistered_guests"] == 0
    assert hour["registered_users"] == [{"id": user["id"], "display_name": "Bootstrap Admin", "email": "admin@example.com"}]


@pytest.mark.parametrize("role", [UserRole.GUEST, UserRole.CONTRIBUTOR])
def test_usage_and_csv_are_admin_only_and_registered_guests_are_named(usage_app, role):
    app, admin = usage_app
    authenticate_admin(admin)
    with TestClient(app) as client:
        for path in ("/api/admin/usage", "/api/admin/usage/export.csv"):
            assert client.get(path).status_code == 401
        user = authenticate_role(admin, client, email="reader@example.com", display_name="Reader", role=role, password="reader-password")["user"]
        client.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
        for path in ("/api/admin/usage", "/api/admin/usage/export.csv"):
            assert client.get(path).status_code == 403
    hour = report(admin)["hours"][0]
    assert hour["registered_users"][0]["id"] == user["id"]
    assert hour["unregistered_guests"] == 0


def test_collection_requires_browser_header_and_cookie_is_private(usage_app):
    _, client = usage_app
    assert client.post("/api/usage/activity").status_code == 403
    response = client.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    original = client.cookies.get("marawa_usage")
    client.cookies.clear()
    client.cookies.set("marawa_usage", "forged.browser")
    client.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
    assert client.cookies.get("marawa_usage", domain="testserver.local", path="/") != original
    assert response.headers["cache-control"] == "no-store"


def test_reading_usage_does_not_itself_record_activity_and_csv_matches(usage_app):
    _, admin = usage_app
    authenticate_admin(admin)
    assert report(admin)["first_recorded_at"] is None
    assert all(hour["total_visitors"] == 0 for hour in report(admin)["hours"])
    admin.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
    response = admin.get("/api/admin/usage/export.csv", params=RANGE)
    assert response.status_code == 200
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.DictReader(StringIO(response.text)))
    assert len(rows) == 3
    assert rows[0]["hour_start_utc"] == "2026-10-13T10:00:00+00:00"
    assert rows[0]["total_visitors"] == "1"
    assert json.loads(rows[0]["registered_users_json"])[0]["email"] == "admin@example.com"
    all_history = admin.get("/api/admin/usage/export.csv")
    assert len(list(csv.DictReader(StringIO(all_history.text)))) == 1


@pytest.mark.parametrize("params", [
    {"start": "2026-10-13T10:00:00", "end": "2026-10-13T11:00:00Z"},
    {"start": "2026-10-13T10:01:00Z", "end": "2026-10-13T11:00:00Z"},
    {"start": "2026-10-13T10:00:00Z", "end": "2026-10-13T10:00:00Z"},
    {"start": "2026-10-13T10:00:00Z"},
    {"start": "2025-10-13T10:00:00Z", "end": "2026-10-13T10:00:00Z"},
])
def test_invalid_or_unbounded_ranges_are_rejected(usage_app, params):
    _, admin = usage_app
    authenticate_admin(admin)
    for path in ("/api/admin/usage", "/api/admin/usage/export.csv"):
        assert admin.get(path, params=params).status_code == 422


def test_concurrent_pings_are_idempotent_and_do_not_move_timestamps_backwards(usage_app):
    app, _ = usage_app
    def ping(minute):
        with app.state.session_factory() as session:
            usage.record_activity(session, browser_key="a" * 64, user=None, now=HOUR + timedelta(minutes=minute))
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(ping, [30, 10, 50, 20, 40, 15]))
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UsageActivity)) == 1
        entry = session.scalar(select(UsageActivity))
        assert usage.ensure_utc(entry.first_seen_at) == HOUR + timedelta(minutes=10)
        assert usage.ensure_utc(entry.last_seen_at) == HOUR + timedelta(minutes=50)


def test_history_survives_restart_and_name_changes(usage_app):
    app, admin = usage_app
    user_id = authenticate_admin(admin)["user"]["id"]
    admin.post("/api/usage/activity", headers=ACTIVITY_HEADERS)
    with app.state.session_factory() as session:
        user = session.get(User, user_id)
        user.display_name = "Changed Name"
        session.commit()
    restarted = create_app(db_engine=app.state.db_engine, session_factory=app.state.session_factory, job_runner_enabled=False)
    with TestClient(restarted) as client:
        authenticate_admin(client)
        assert report(client)["hours"][0]["registered_users"][0]["display_name"] == "Bootstrap Admin"


def test_shared_browser_preserves_both_accounts(usage_app):
    app, _ = usage_app
    with app.state.session_factory() as session:
        users = [User(email=f"user{number}@example.com", display_name=f"Person {number}", status=UserStatus.ACTIVE) for number in (1, 2)]
        session.add_all(users)
        session.commit()
        for user in users:
            usage.record_activity(session, browser_key="shared", user=user, now=HOUR)
        usage.record_activity(session, browser_key="shared", user=None, now=HOUR)
        data = usage.get_usage_report(session, HOUR, HOUR + timedelta(hours=1))
    assert data["hours"][0]["total_visitors"] == 2
    assert data["hours"][0]["unregistered_guests"] == 0


def test_expired_auth_session_is_counted_as_anonymous(usage_app):
    app, client = usage_app
    authenticate_admin(client)
    with app.state.session_factory() as session:
        for user_session in session.scalars(select(UserSession)):
            user_session.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        session.commit()
    assert client.post("/api/usage/activity", headers=ACTIVITY_HEADERS).status_code == 204
    authenticate_admin(client)
    hour = report(client)["hours"][0]
    assert hour["unregistered_guests"] == 1
    assert hour["registered_users"] == []


def test_malformed_browser_cookies_are_replaced_without_exposing_tokens(usage_app):
    app, _ = usage_app
    for cookie in ("not-a-cookie", "a" * 64 + ".é", "a" * 64 + "." + "0" * 64):
        signed, key = usage.browser_identity(get_settings(), cookie)
        assert signed != cookie
        assert usage.browser_identity(get_settings(), signed) == (signed, key)
        with app.state.session_factory() as session:
            usage.record_activity(session, browser_key=key, user=None, now=HOUR)
            entry = session.get(UsageActivity, (HOUR, key, "anonymous"))
            assert entry.browser_key not in signed
