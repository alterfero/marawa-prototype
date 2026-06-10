from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import build_engine, build_session_factory
from app.main import create_app
from tests.search_fakes import FakeEmbeddingBackend


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    import csv
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def test_search_api_returns_similar_tropes_and_keywords(tmp_path) -> None:
    db_path = tmp_path / "search-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )

    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story"
    row[TROPE_FIELD] = "§§ first trope\n§§ first trope variant\n§§ second trope"
    row[KEYWORD_FIELD] = "wolf ; moon ; river"

    with TestClient(app) as client:
        upload_response = client.post(
            "/api/dataset/upload",
            files={"file": ("search.csv", make_csv_bytes([row]), "text/csv")},
        )
        assert upload_response.status_code == 201

        processed = client.app.state.job_runner.process_next_job()
        assert processed is True

        trope_response = client.post("/api/search/tropes", json={"query": "first trope", "limit": 3})
        keyword_response = client.post("/api/search/keywords", json={"query": "wolf", "limit": 3})

    assert trope_response.status_code == 200
    trope_body = trope_response.json()
    assert trope_body["model_name"] == FakeEmbeddingBackend.model_name
    assert trope_body["artifact_version"] == 1
    assert trope_body["items"][0]["text"] == "first trope"
    assert trope_body["items"][1]["text"] == "first trope variant"
    assert trope_body["items"][1]["explanation"]["cache_hit"] is True

    assert keyword_response.status_code == 200
    keyword_body = keyword_response.json()
    assert keyword_body["items"][0]["text"] == "wolf"
    assert keyword_body["items"][1]["text"] == "moon"
