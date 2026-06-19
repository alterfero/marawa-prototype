import csv
import io

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env


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


def test_trope_detail_lists_story_titles_for_active_dataset(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    db_path = tmp_path / "tropes-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", tropes="§§ first trope"),
                make_row(title="Story Two", tropes="§§ second trope\n§§ first trope"),
                make_row(title="Story Three", tropes="§§ second trope"),
            ],
        )

        trope_list = client.get("/api/tropes").json()
        first_trope = next(item for item in trope_list if item["text"] == "first trope")

        response = client.get(f"/api/tropes/{first_trope['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == first_trope["id"]
    assert body["text"] == "first trope"
    assert body["story_count"] == 2
    assert [item["title"] for item in body["stories"]] == ["Story One", "Story Two"]


def test_create_canonical_trope_creates_unused_trope(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    db_path = tmp_path / "tropes-create-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope")])

        response = client.post("/api/tropes", json={"text": "Moon Bride"})

        trope_list = client.get("/api/tropes").json()

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["trope"]["text"] == "Moon Bride"
    assert body["trope"]["story_count"] == 0
    assert any(item["id"] == body["trope"]["id"] and item["text"] == "Moon Bride" for item in trope_list)


def test_create_canonical_trope_reuses_existing_trope_by_normalized_text(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    db_path = tmp_path / "tropes-reuse-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", tropes="§§ moon bride")])

        trope_list = client.get("/api/tropes").json()
        existing = next(item for item in trope_list if item["text"] == "moon bride")

        response = client.post("/api/tropes", json={"text": "  Moon Bride  "})

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is False
    assert body["trope"]["id"] == existing["id"]
    assert body["trope"]["text"] == "moon bride"
    assert body["trope"]["story_count"] == 1
