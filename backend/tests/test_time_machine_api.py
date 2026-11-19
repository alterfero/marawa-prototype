import csv
import gzip
import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, MARAWA_TERM_CATALOG_FIELD, THEME_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, Story, Trope, build_engine, build_session_factory
from app.db.models import DatasetSnapshot, TropeParent
from app.main import create_app
from app.services.csv_io import CSVImportValidationError
import app.services.snapshots as snapshot_service_module
from tests.auth_helpers import authenticate_admin, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


def make_csv_bytes(*, title: str, trope: str, keyword: str, theme: str = "") -> bytes:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = trope
    row[KEYWORD_FIELD] = keyword
    row[THEME_FIELD] = theme
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


def upload_and_rebuild(client: TestClient, *, title: str, trope: str, keyword: str, theme: str = "") -> None:
    upload = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(title=title, trope=trope, keyword=keyword, theme=theme), "text/csv")},
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
        "changes": {
            "stories": {"current_only": [], "checkpoint_only": []},
            "tropes": {"current_only": [], "checkpoint_only": []},
            "themes": {"current_only": [], "checkpoint_only": []},
            "keywords": {"current_only": [], "checkpoint_only": []},
        },
    }


def test_checkpoint_preview_lists_terms_and_story_titles_that_would_change(client: TestClient) -> None:
    upload_and_rebuild(
        client,
        title="Checkpoint story",
        trope="§§ Checkpoint trope",
        keyword="checkpoint keyword",
        theme="§§ Checkpoint theme",
    )
    snapshot = client.get("/api/time-machine").json()[0]

    upload_and_rebuild(
        client,
        title="Current story",
        trope="§§ Current trope",
        keyword="current keyword",
        theme="§§ Current theme",
    )

    detail = client.get(f"/api/time-machine/{snapshot['id']}")

    assert detail.status_code == 200
    assert detail.json()["difference_from_current"]["changes"] == {
        "stories": {
            "current_only": [{"text": "Current story", "count": 1}],
            "checkpoint_only": [{"text": "Checkpoint story", "count": 1}],
        },
        "tropes": {
            "current_only": [{"text": "Current trope", "count": 1}],
            "checkpoint_only": [{"text": "Checkpoint trope", "count": 1}],
        },
        "themes": {
            "current_only": [{"text": "Current theme", "count": 1}],
            "checkpoint_only": [{"text": "Checkpoint theme", "count": 1}],
        },
        "keywords": {
            "current_only": [{"text": "current keyword", "count": 1}],
            "checkpoint_only": [{"text": "checkpoint keyword", "count": 1}],
        },
    }


def test_specificity_and_link_edits_survive_rebuild_snapshot_and_recovery(client: TestClient) -> None:
    upload_and_rebuild(
        client,
        title="Transformation story",
        trope="§§ a non-human turns into a man\n§§ an animal turns into a man\n§§ a snake turns into a man",
        keyword="transformation",
    )

    def edit_ontology(kind: str, **edit) -> None:
        graph = client.get("/api/ontology").json()
        response = client.put(f"/api/ontology/{kind}", json={
            "dataset_id": graph["dataset_id"], "expected_version": graph["version"], **edit,
        })
        assert response.status_code == 200, response.text

    def stored_ontology() -> tuple[dict, set]:
        # A fresh session verifies that each API action committed its changes.
        with client.app.state.session_factory() as session:
            dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
            tropes = session.scalars(select(Trope).where(Trope.dataset_id == dataset.id)).all()
            stable_ids = {trope.id: trope.ontology_id for trope in tropes}
            ratings = {
                trope.text: (trope.ontology_id, trope.specificity_level, trope.specificity_source)
                for trope in tropes
            }
            links = {
                (stable_ids[link.child_id], stable_ids[link.parent_id], link.status)
                for link in session.scalars(select(TropeParent).where(TropeParent.dataset_id == dataset.id))
            }
            return ratings, links

    graph = client.get("/api/ontology").json()
    ids = {trope["text"]: trope["id"] for trope in graph["tropes"]}
    child_id = ids["a snake turns into a man"]
    parent_id = ids["an animal turns into a man"]
    edit_ontology("level", trope_id=child_id, level=4)
    edit_ontology("link", child_id=child_id, parent_id=parent_id, status="approved")
    edit_ontology("link", child_id=parent_id, parent_id=ids["a non-human turns into a man"], status="rejected")

    # Unassigned catalogue tropes must be archived too, at every supported level.
    for level in range(1, 5):
        response = client.post("/api/tropes", json={
            "text": f"Unassigned catalogue trope {level}", "specificity_level": level,
        })
        assert response.status_code == 200, response.text
        assert stored_ontology()[0][f"Unassigned catalogue trope {level}"][1:] == (level, "curator")

    expected_ratings, expected_links = stored_ontology()
    assert expected_ratings["a snake turns into a man"][1:] == (4, "curator")
    assert {status for _, _, status in expected_links} == {"approved", "rejected"}
    assert client.post("/api/dataset/rebuild").status_code == 200
    assert client.app.state.job_runner.process_next_job() is True
    assert stored_ontology() == (expected_ratings, expected_links)

    snapshot = client.get("/api/time-machine").json()[0]
    assert snapshot["sequence"] == 2
    with client.app.state.session_factory() as session:
        record = session.get(DatasetSnapshot, snapshot["id"])
        archive = client.app.state.snapshot_service.storage.get(record.object_key)
    rows = csv.DictReader(io.StringIO(gzip.decompress(archive).decode("utf-8-sig")))
    catalog = json.loads(next(rows)[MARAWA_TERM_CATALOG_FIELD])
    assert {
        trope["text"]: (trope["ontology"]["id"], trope["ontology"]["level"], trope["ontology"]["source"])
        for trope in catalog["tropes"]
    } == expected_ratings
    assert {
        (trope["ontology"]["id"], link["id"], link["status"])
        for trope in catalog["tropes"] for link in trope["ontology"]["broader"]
    } == expected_links

    # Recovery must restore the checkpoint's ratings and decisions over later edits.
    edit_ontology("link", child_id=child_id, parent_id=parent_id, status="rejected")
    edit_ontology("level", trope_id=child_id, level=1)
    assert stored_ontology() != (expected_ratings, expected_links)
    restore = client.post(f"/api/time-machine/{snapshot['id']}/restore")
    assert restore.status_code == 202, restore.text
    assert client.app.state.job_runner.process_next_job() is True
    job = client.get(f"/api/jobs/{restore.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    assert stored_ontology() == (expected_ratings, expected_links)


def test_restore_job_reports_the_snapshot_validation_detail(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    upload_and_rebuild(client, title="First story", trope="§§ First trope", keyword="first")
    snapshot = client.get("/api/time-machine").json()[0]

    def reject_snapshot(*_args, **_kwargs):
        raise CSVImportValidationError("The Marawa term catalog has an unsupported schema version.")

    monkeypatch.setattr(snapshot_service_module, "import_csv_bytes", reject_snapshot)
    restore = client.post(f"/api/time-machine/{snapshot['id']}/restore")

    assert restore.status_code == 202
    assert client.app.state.job_runner.process_next_job() is True
    job = client.get(f"/api/jobs/{restore.json()['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "failed"
    assert job.json()["error_message"] == (
        "The selected snapshot cannot be read by this version of Marawa: "
        "The Marawa term catalog has an unsupported schema version."
    )


def test_restore_uses_snapshot_metadata_when_a_trope_has_a_legacy_delimiter(client: TestClient) -> None:
    upload_and_rebuild(client, title="Delimited trope story", trope="§§ sun", keyword="sun")

    with client.app.state.session_factory() as session:
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        assert active_dataset is not None
        trope = session.scalar(select(Trope).where(Trope.dataset_id == active_dataset.id))
        assert trope is not None
        trope.text = "sun §§ moon"
        session.commit()
        snapshot = client.app.state.snapshot_service.capture(session, dataset=active_dataset, reason="test")
        session.commit()
        snapshot_id = snapshot.id

    restore = client.post(f"/api/time-machine/{snapshot_id}/restore")

    assert restore.status_code == 202
    assert client.app.state.job_runner.process_next_job() is True
    job = client.get(f"/api/jobs/{restore.json()['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded", job.json()

    with client.app.state.session_factory() as session:
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        assert active_dataset is not None
        active_trope = session.scalar(select(Trope).where(Trope.dataset_id == active_dataset.id))
        assert active_trope is not None
        assert active_trope.text == "sun §§ moon"


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
