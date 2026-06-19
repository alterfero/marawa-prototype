import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, UserRole, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


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
    return create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )


def test_anonymous_and_guest_access_matrix(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "rbac-guest.db")

    with TestClient(app) as admin_client:
        authenticate_admin(admin_client)
        upload_dataset(admin_client, [make_row(title="Story One", tropes="§§ first trope")])

    with TestClient(app) as guest_client, TestClient(app) as anonymous_client:
        with TestClient(app) as admin_client:
            authenticate_admin(admin_client)
            authenticate_role(
                admin_client,
                guest_client,
                email="guest@example.com",
                display_name="Guest User",
                role=UserRole.GUEST,
                password="guest-password",
            )

        stories_response = guest_client.get("/api/stories")
        mutate_response = guest_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "Blocked"},
                "tropes": [],
                "keywords": [],
            },
        )
        exploration_response = anonymous_client.post(
            "/api/exploration/network",
            json={"query": "first trope"},
        )
        anonymous_stories = anonymous_client.get("/api/stories")

    assert stories_response.status_code == 200
    assert stories_response.json()["total"] == 1
    assert mutate_response.status_code == 403
    assert mutate_response.json()["code"] == "forbidden"
    assert exploration_response.status_code == 200
    assert anonymous_stories.status_code == 401
    assert anonymous_stories.json()["code"] == "authentication_required"


def test_contributor_and_admin_access_matrix(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "rbac-contributor.db")

    with TestClient(app) as admin_client:
        authenticate_admin(admin_client)
        upload_dataset(admin_client, [make_row(title="Story One", keywords="moon")])

    with TestClient(app) as contributor_client:
        with TestClient(app) as admin_client:
            authenticate_admin(admin_client)
            authenticate_role(
                admin_client,
                contributor_client,
                email="contributor@example.com",
                display_name="Contributor User",
                role=UserRole.CONTRIBUTOR,
                password="contributor-password",
            )

            export_response = admin_client.get("/api/dataset/export.csv")

        create_story_response = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "Contributor Story"},
                "tropes": ["Moon Bride"],
                "keywords": ["night canoe"],
            },
        )
        create_keyword_response = contributor_client.post("/api/keywords", json={"text": "Breadfruit"})
        upload_response = contributor_client.post(
            "/api/dataset/upload",
            files={"file": ("stories.csv", make_csv_bytes([make_row(title='Blocked Upload')]), "text/csv")},
        )
        curation_response = contributor_client.get("/api/curation/near-duplicate-tropes")

    assert export_response.status_code == 200
    assert create_story_response.status_code == 201
    assert create_story_response.json()["story"]["fields"]["Story title (Eng)"] == "Contributor Story"
    assert create_keyword_response.status_code == 200
    assert create_keyword_response.json()["created"] is True
    assert upload_response.status_code == 403
    assert upload_response.json()["code"] == "forbidden"
    assert curation_response.status_code == 403
    assert curation_response.json()["code"] == "forbidden"
