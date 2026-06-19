import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import AuditEvent, Dataset, DatasetStatus, UserRole, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(*, title: str, tropes: str = "", keywords: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    return row


def upload_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201
    with client.app.state.session_factory() as session:
        staged_dataset = session.scalar(
            select(Dataset)
            .where(Dataset.status == DatasetStatus.STAGED)
            .order_by(Dataset.created_at.desc(), Dataset.id.desc())
        )
        assert staged_dataset is not None
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        if active_dataset is not None and active_dataset.id != staged_dataset.id:
            active_dataset.status = DatasetStatus.ARCHIVED
        staged_dataset.status = DatasetStatus.ACTIVE
        session.commit()


def build_app(tmp_path, name: str):
    db_path = tmp_path / name
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    return create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)


def test_audit_events_capture_auth_admin_write_and_review_actions(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "audit-events.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_dataset(admin_client, [make_row(title="Imported Story")])
        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Contributor User",
            role=UserRole.CONTRIBUTOR,
            password="contributor-password",
        )

        create_story = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "Contributor Story"},
                "tropes": ["Audit Pending Trope"],
                "keywords": ["Audit Keyword"],
            },
        )
        assert create_story.status_code == 201

        review_items = admin_client.get("/api/review/items").json()
        story_review = next(item for item in review_items if item["review_type"] == "story_created")
        keyword_review = next(item for item in review_items if item["review_type"] == "keyword_pending")

        approve_story = admin_client.post(
            f"/api/review/items/{story_review['id']}/approve",
            json={"note": "Looks good"},
        )
        assert approve_story.status_code == 200

        reject_keyword = admin_client.post(
            f"/api/review/items/{keyword_review['id']}/reject",
            json={"remove_from_all_stories": True},
        )
        assert reject_keyword.status_code == 200

        with admin_client.app.state.session_factory() as session:
            event_types = [event.event_type for event in session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())).all()]

    assert "auth.login" in event_types
    assert "dataset.uploaded" in event_types
    assert "user.created" in event_types
    assert "auth.invite_redeemed" in event_types
    assert "story.created" in event_types
    assert "trope.created" in event_types
    assert "keyword.created" in event_types
    assert "review.approved" in event_types
    assert "review.rejected" in event_types
