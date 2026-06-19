import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD, TROPE_PROPOSAL_FIELD
from app.db import (
    Dataset,
    Job,
    Keyword,
    Story,
    StoryKeyword,
    StoryTrope,
    TermEmbedding,
    TermSimilarityCache,
    Trope,
    build_engine,
    build_session_factory,
)
from app.main import create_app
from tests.search_fakes import FakeEmbeddingBackend
from tests.auth_helpers import authenticate_admin, configure_auth_env


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def make_csv_bytes(rows: list[dict[str, str]], *, fieldnames: list[str] | None = None) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames or CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_current_template_fieldnames() -> list[str]:
    return [
        "Entered by",
        "Source first or second hand",
        "Source",
        "pages",
        "Other source",
        "URL ?",
        "territory",
        "lg group",
        "original language",
        "lg of publication",
        "bilingual?",
        "storyteller",
        "date of recording ",
        "place of recording",
        "space coord",
        "editor",
        "translator",
        "Story title (Eng)",
        "Story title (French)",
        "Story title (other)",
        "1-sentence summary",
        "Abstracts : AI or Human ?",
        "Abstract (Eng)",
        "Abstract (Fr)",
        "Keywords (Eng)",
        "Motifs (Eng)",
        "motifs inhabituels à une version",
        "Motifs validés ",
        "species",
        "non-human",
        "placenames",
        "named characters",
        "external link",
        "description of link",
        "Connection to other stories",
        "Megamotifs",
        "Thème ",
        "Conte type",
        "Autres infos données dans le texte, pour la fiche conte ",
        "ATU conte-type(AI ?)",
        "ATU motifs (AI?)",
        "motifs Pacifique  ?",
    ]


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    configure_auth_env(monkeypatch)
    db_path = tmp_path / "api.db"
    engine = build_engine(f"sqlite:///{db_path}")
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


def test_dataset_status_returns_empty_state_when_no_active_dataset(client: TestClient) -> None:
    response = client.get("/api/dataset/status")

    assert response.status_code == 200
    assert response.json() == {
        "story_count": 0,
        "trope_count": 0,
        "keyword_count": 0,
        "active_dataset_version": None,
        "latest_job": None,
        "embedding_status": {
            "state": "missing",
            "ready": False,
            "current": False,
            "model_name": FakeEmbeddingBackend.model_name,
            "artifact_version": None,
            "rebuilt_dataset_version": None,
            "indexed_trope_count": 0,
            "indexed_keyword_count": 0,
            "last_built_at": None,
            "last_error_message": None,
            "latest_rebuild_job": None,
        },
    }


def test_dataset_upload_stages_csv_and_queues_placeholder_rebuild_job(client: TestClient) -> None:
    first_row = {column: "" for column in CSV_COLUMNS}
    first_row["Story title (Eng)"] = "Story One"
    first_row[KEYWORD_FIELD] = "wolf ; moon"
    first_row[TROPE_FIELD] = "§§ first trope\n§§ second trope"

    second_row = {column: "" for column in CSV_COLUMNS}
    second_row["Story title (Eng)"] = "Story Two"
    second_row[KEYWORD_FIELD] = "river"
    second_row[TROPE_FIELD] = "§§ second trope\n§§ third trope"

    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes([first_row, second_row]), "text/csv")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["dataset_version"] == 1
    assert body["dataset_status"] == "staged"
    assert body["active_dataset_version"] is None
    assert body["latest_job"]["status"] == "queued"
    assert body["latest_job"]["job_type"] == "full_rebuild"

    status_response = client.get("/api/dataset/status")
    assert status_response.status_code == 200
    assert status_response.json()["story_count"] == 0
    assert status_response.json()["trope_count"] == 0
    assert status_response.json()["keyword_count"] == 0
    assert status_response.json()["active_dataset_version"] is None
    assert status_response.json()["latest_job"]["status"] == "queued"
    assert status_response.json()["embedding_status"]["state"] == "missing"
    assert status_response.json()["embedding_status"]["ready"] is False
    assert status_response.json()["embedding_status"]["current"] is False
    assert status_response.json()["embedding_status"]["latest_rebuild_job"]["status"] == "queued"


def test_dataset_upload_accepts_current_template_and_preserves_legacy_export_fields(client: TestClient) -> None:
    fieldnames = make_current_template_fieldnames()
    row = {column: "" for column in fieldnames}
    row["Story title (Eng)"] = "Template Story"
    row[KEYWORD_FIELD] = "wolf ; moon"
    row[TROPE_FIELD] = "§§ first trope\n§§ second trope"
    row["motifs inhabituels à une version"] = "new trope idea"
    row["Abstracts : AI or Human ?"] = "Human"
    row["Motifs validés "] = "ok"

    response = client.post(
        "/api/dataset/upload",
        files={"file": ("template.csv", make_csv_bytes([row], fieldnames=fieldnames), "text/csv")},
    )

    assert response.status_code == 201
    assert client.app.state.job_runner.process_next_job() is True

    stories_response = client.get("/api/stories")
    assert stories_response.status_code == 200
    story = stories_response.json()["items"][0]
    detail_response = client.get(f"/api/stories/{story['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()

    assert detail["fields"]["Story title (Eng)"] == "Template Story"
    assert detail["fields"][TROPE_PROPOSAL_FIELD] == "new trope idea"
    assert "Abstracts : AI or Human ?" not in detail["fields"]


def test_dataset_status_reports_embeddings_ready_and_current_after_rebuild(client: TestClient) -> None:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story"
    row[KEYWORD_FIELD] = "wolf ; moon"
    row[TROPE_FIELD] = "§§ first trope\n§§ second trope"

    upload_response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes([row]), "text/csv")},
    )
    assert upload_response.status_code == 201

    processed = client.app.state.job_runner.process_next_job()
    assert processed is True

    status_response = client.get("/api/dataset/status")

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["embedding_status"]["state"] == "ready"
    assert body["embedding_status"]["ready"] is True
    assert body["embedding_status"]["current"] is True
    assert body["embedding_status"]["model_name"] == FakeEmbeddingBackend.model_name
    assert body["embedding_status"]["artifact_version"] == 1
    assert body["embedding_status"]["rebuilt_dataset_version"] == 1
    assert body["embedding_status"]["indexed_trope_count"] == 2
    assert body["embedding_status"]["indexed_keyword_count"] == 2
    assert body["embedding_status"]["latest_rebuild_job"]["status"] == "succeeded"


def test_dataset_status_marks_embeddings_not_current_after_story_mutation(client: TestClient) -> None:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story"
    row[TROPE_FIELD] = "§§ first trope"

    upload_response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes([row]), "text/csv")},
    )
    assert upload_response.status_code == 201
    assert client.app.state.job_runner.process_next_job() is True

    stories_response = client.get("/api/stories")
    story = stories_response.json()["items"][0]
    mutate_response = client.post(
        f"/api/stories/{story['id']}/tropes",
        json={"expected_story_version": 1, "text": "second trope"},
    )
    assert mutate_response.status_code == 201

    status_response = client.get("/api/dataset/status")

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["active_dataset_version"] == 2
    assert body["embedding_status"]["state"] == "queued"
    assert body["embedding_status"]["ready"] is True
    assert body["embedding_status"]["current"] is False
    assert body["embedding_status"]["rebuilt_dataset_version"] == 1
    assert body["embedding_status"]["latest_rebuild_job"]["status"] == "queued"


def test_dataset_upload_returns_clear_validation_error_for_invalid_header(client: TestClient) -> None:
    invalid_csv = "Story title (Eng),Keywords (Eng)\nStory,wolf\n".encode("utf-8")

    response = client.post(
        "/api/dataset/upload",
        files={"file": ("invalid.csv", invalid_csv, "text/csv")},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "csv_import_invalid"
    assert "missing required legacy columns" in body["message"]


def test_dataset_upload_rejects_malformed_csv_with_clear_error(client: TestClient) -> None:
    header = make_csv_bytes([]).decode("utf-8-sig")
    malformed_csv = f'{header}"broken title"value'.encode("utf-8")

    response = client.post(
        "/api/dataset/upload",
        files={"file": ("broken.csv", malformed_csv, "text/csv")},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "csv_import_invalid"
    assert "malformed" in body["message"].lower()


def test_dataset_upload_rejects_files_larger_than_configured_limit(tmp_path, monkeypatch) -> None:
    configure_auth_env(monkeypatch)
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "64")
    get_settings.cache_clear()

    db_path = tmp_path / "upload-limit.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    payload = make_csv_bytes([{column: "x" * 12 for column in CSV_COLUMNS}])
    with TestClient(app) as client:
        authenticate_admin(client)
        response = client.post(
            "/api/dataset/upload",
            files={"file": ("too-large.csv", payload, "text/csv")},
        )

    assert response.status_code == 413
    body = response.json()
    assert body["code"] == "file_too_large"
    assert body["details"] == {"max_upload_bytes": 64}
    get_settings.cache_clear()


def test_dataset_export_downloads_current_dataset_as_csv(client: TestClient) -> None:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Exported Story"
    row[KEYWORD_FIELD] = "canoe\ncanoe ; sea"
    row[TROPE_FIELD] = "first trope ; second trope\nfirst trope"

    upload_response = client.post(
        "/api/dataset/upload",
        files={"file": ("export.csv", make_csv_bytes([row]), "text/csv")},
    )
    assert upload_response.status_code == 201
    assert client.app.state.job_runner.process_next_job() is True

    response = client.get("/api/dataset/export.csv")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="dataset-export.csv"'
    assert response.headers["content-type"].startswith("text/csv")

    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    rows = list(reader)

    assert reader.fieldnames == CSV_COLUMNS
    assert len(rows) == 1
    assert rows[0]["Story title (Eng)"] == "Exported Story"
    assert rows[0][KEYWORD_FIELD] == "canoe ; sea"
    assert rows[0][TROPE_FIELD] == "§§ first trope\n§§ second trope"


def test_clear_dataset_removes_current_data_and_returns_empty_state(client: TestClient) -> None:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story to clear"
    row[KEYWORD_FIELD] = "canoe ; sea"
    row[TROPE_FIELD] = "§§ first trope\n§§ second trope"

    upload_response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes([row]), "text/csv")},
    )
    assert upload_response.status_code == 201
    assert client.app.state.job_runner.process_next_job() is True

    clear_response = client.delete("/api/dataset")

    assert clear_response.status_code == 200
    assert clear_response.json() == {
        "story_count": 0,
        "trope_count": 0,
        "keyword_count": 0,
        "active_dataset_version": None,
        "latest_job": None,
        "embedding_status": {
            "state": "missing",
            "ready": False,
            "current": False,
            "model_name": FakeEmbeddingBackend.model_name,
            "artifact_version": None,
            "rebuilt_dataset_version": None,
            "indexed_trope_count": 0,
            "indexed_keyword_count": 0,
            "last_built_at": None,
            "last_error_message": None,
            "latest_rebuild_job": None,
        },
    }

    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Dataset)) == 0
        assert session.scalar(select(func.count()).select_from(Story)) == 0
        assert session.scalar(select(func.count()).select_from(StoryTrope)) == 0
        assert session.scalar(select(func.count()).select_from(StoryKeyword)) == 0
        assert session.scalar(select(func.count()).select_from(Trope)) == 0
        assert session.scalar(select(func.count()).select_from(Keyword)) == 0
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(TermEmbedding)) == 0
        assert session.scalar(select(func.count()).select_from(TermSimilarityCache)) == 0

    export_response = client.get("/api/dataset/export.csv")
    assert export_response.status_code == 404
    assert export_response.json()["code"] == "active_dataset_not_found"

    reupload_response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes([row]), "text/csv")},
    )

    assert reupload_response.status_code == 201
    assert reupload_response.json()["dataset_status"] == "staged"
    assert reupload_response.json()["active_dataset_version"] is None
