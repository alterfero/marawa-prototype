import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env


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


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    configure_auth_env(monkeypatch)
    db_path = tmp_path / "keywords-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as test_client:
        authenticate_admin(test_client)
        yield test_client


def test_keywords_api_lists_details_and_reuses_existing_keyword(client: TestClient) -> None:
    upload_dataset(
        client,
        [
            make_row(title="Story One", keywords="wolf ; moon"),
            make_row(title="Story Two", keywords="moon"),
        ],
    )

    list_response = client.get("/api/keywords?q=moon")
    assert list_response.status_code == 200
    listed_keywords = list_response.json()
    assert len(listed_keywords) == 1
    assert listed_keywords[0]["text"] == "moon"
    assert listed_keywords[0]["story_count"] == 2

    keyword_id = listed_keywords[0]["id"]
    detail_response = client.get(f"/api/keywords/{keyword_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == keyword_id
    assert detail["text"] == "moon"
    assert detail["story_count"] == 2
    assert [item["title"] for item in detail["stories"]] == ["Story One", "Story Two"]

    create_response = client.post("/api/keywords", json={"text": "  Moon  "})
    assert create_response.status_code == 200
    body = create_response.json()
    assert body["created"] is False
    assert body["keyword"]["id"] == keyword_id
    assert body["keyword"]["text"] == "moon"
    assert body["keyword"]["story_count"] == 2


def test_keywords_api_creates_unused_keyword_and_filters_unused_only(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Story One", keywords="wolf")])

    create_response = client.post("/api/keywords", json={"text": "Night Canoe"})
    assert create_response.status_code == 200
    body = create_response.json()
    assert body["created"] is True
    assert body["keyword"]["text"] == "Night Canoe"
    assert body["keyword"]["story_count"] == 0

    unused_response = client.get("/api/keywords?unused_only=true&q=night")
    assert unused_response.status_code == 200
    assert unused_response.json() == [
        {
            "id": body["keyword"]["id"],
            "text": "Night Canoe",
            "story_count": 0,
        }
    ]
