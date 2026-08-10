import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, Story, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


def make_csv_bytes(*, title: str, trope: str, keyword: str) -> bytes:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = trope
    row[KEYWORD_FIELD] = keyword
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    configure_auth_env(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("SNAPSHOT_RETENTION_COUNT", "50")
    get_settings.cache_clear()
    engine = build_engine(f"sqlite:///{tmp_path / 'time-machine.db'}")
    session_factory = build_session_factory(engine)
    app = create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )
    with TestClient(app) as test_client:
        authenticate_admin(test_client)
        yield test_client


def upload_and_rebuild(client: TestClient, *, title: str, trope: str, keyword: str) -> None:
    upload = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(title=title, trope=trope, keyword=keyword), "text/csv")},
    )
    assert upload.status_code == 201
    rebuild = client.post("/api/dataset/rebuild")
    assert rebuild.status_code == 200
    assert client.app.state.job_runner.process_next_job() is True


def test_successful_rebuild_creates_admin_visible_logical_snapshot(client: TestClient) -> None:
    upload_and_rebuild(client, title="First story", trope="§§ First trope", keyword="first")

    response = client.get("/api/time-machine")

    assert response.status_code == 200
    snapshots = response.json()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["sequence"] == 1
    assert snapshot["status"] == "ready"
    assert snapshot["reason"] == "full_rebuild"
    assert snapshot["counts"] == {"stories": 1, "tropes": 1, "themes": 0, "keywords": 1}
    assert snapshot["source_job_id"] is not None
    assert snapshot["content_length"] is not None

    detail = client.get(f"/api/time-machine/{snapshot['id']}")
    assert detail.status_code == 200
    assert detail.json()["difference_from_current"] == {
        "current_dataset_version": 1,
        "story_count_delta": 0,
        "trope_count_delta": 0,
        "theme_count_delta": 0,
        "keyword_count_delta": 0,
    }


def test_restore_stages_and_promotes_snapshot_without_deleting_current_revision(client: TestClient) -> None:
    upload_and_rebuild(client, title="First story", trope="§§ First trope", keyword="first")
    first_snapshot = client.get("/api/time-machine").json()[0]

    upload_and_rebuild(client, title="Second story", trope="§§ Second trope", keyword="second")
    restore = client.post(f"/api/time-machine/{first_snapshot['id']}/restore")

    assert restore.status_code == 202
    body = restore.json()
    assert body["snapshot"]["id"] == first_snapshot["id"]
    assert body["safety_snapshot"]["reason"] == "pre_restore"
    assert body["job_status"] == "queued"
    assert client.app.state.job_runner.process_next_job() is True
    restore_job = client.get(f"/api/jobs/{body['job_id']}")
    assert restore_job.status_code == 200
    assert restore_job.json()["status"] == "succeeded", restore_job.json()

    with client.app.state.session_factory() as session:
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        assert active_dataset is not None
        active_title = session.scalar(
            select(Story.fields_json["Story title (Eng)"]).where(Story.dataset_id == active_dataset.id)
        )
        assert active_title == "First story"
        archived_count = len(
            list(session.scalars(select(Dataset).where(Dataset.status == DatasetStatus.ARCHIVED)).all())
        )
        assert archived_count == 2

    snapshots = client.get("/api/time-machine").json()
    assert len(snapshots) == 4
    assert snapshots[0]["reason"] == "full_rebuild"
    assert {snapshot["reason"] for snapshot in snapshots} == {"full_rebuild", "pre_restore"}


def test_snapshot_retention_is_fifo_by_checkpoint_sequence(client: TestClient) -> None:
    client.app.state.snapshot_service.retention_count = 2

    upload_and_rebuild(client, title="First story", trope="§§ First trope", keyword="first")
    upload_and_rebuild(client, title="Second story", trope="§§ Second trope", keyword="second")
    upload_and_rebuild(client, title="Third story", trope="§§ Third trope", keyword="third")

    snapshots = client.get("/api/time-machine").json()

    assert [snapshot["sequence"] for snapshot in snapshots] == [3, 2]


def test_time_machine_endpoints_require_an_admin_session(client: TestClient) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)

    response = client.get("/api/time-machine")

    assert response.status_code == 401
