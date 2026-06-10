import csv
import io

from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import build_engine, build_session_factory
from app.main import create_app
from tests.search_fakes import FakeEmbeddingBackend


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(*, title: str, tropes: str = "", keywords: str = "", coord: str = "", abstract: str = "", summary: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    row["space coord"] = coord
    row["Abstract (Eng)"] = abstract
    row["1-sentence summary"] = summary
    return row


def build_client(tmp_path, name: str) -> TestClient:
    db_path = tmp_path / name
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )
    return TestClient(app)


def upload_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201


def process_next_job(client: TestClient) -> None:
    assert client.app.state.job_runner.process_next_job() is True


def test_exploration_network_returns_trope_candidates_when_selected_trope_is_missing(tmp_path) -> None:
    with build_client(tmp_path, "exploration-candidates.db") as client:
        upload_dataset(
            client,
            [
                make_row(title="Story One", tropes="§§ first trope"),
                make_row(title="Story Two", tropes="§§ first trope variant"),
            ],
        )
        process_next_job(client)

        response = client.post(
            "/api/exploration/network",
            json={
                "query": "first trope",
                "min_similarity": 0.6,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"] is None
    assert body["original_markers"] == []
    assert body["related_markers"] == []
    assert body["connections"] == []
    assert body["selected_trope_candidates"][0]["text"] == "first trope"
    assert body["selected_trope_candidates"][1]["text"] == "first trope variant"


def test_exploration_network_returns_lexical_fallback_candidates_before_rebuild(tmp_path) -> None:
    with build_client(tmp_path, "exploration-candidates-fallback.db") as client:
        upload_dataset(
            client,
            [
                make_row(title="Story One", tropes="§§ first trope"),
                make_row(title="Story Two", tropes="§§ first trope variant"),
                make_row(title="Story Three", tropes="§§ second trope"),
            ],
        )

        response = client.post(
            "/api/exploration/network",
            json={
                "query": "first trope",
                "min_similarity": 0.6,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"] is None
    assert body["selected_trope_candidates"][0]["text"] == "first trope"
    assert body["selected_trope_candidates"][1]["text"] == "first trope variant"


def test_exploration_network_builds_markers_connections_and_bounds(tmp_path) -> None:
    with build_client(tmp_path, "exploration-network.db") as client:
        upload_dataset(
            client,
            [
                make_row(title="Original A", tropes="§§ first trope", coord="-20.0, 165.0", abstract="Original abstract"),
                make_row(title="Original Missing", tropes="§§ first trope", coord="unknown", summary="Fallback summary"),
                make_row(title="Overlap", tropes="§§ first trope\n§§ first trope variant", coord="-19.5, 165.5"),
                make_row(title="Related A", tropes="§§ first trope variant", coord="≈ 16.0° S, 168.4° E"),
                make_row(title="Related B", tropes="§§ first trope variant\n§§ first echo trope", coord="22.2994° ; 166.7483°"),
                make_row(title="Related Missing", tropes="§§ first echo trope", coord="missing"),
            ],
        )
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        selected_story_id = next(item["id"] for item in stories if item["title"] == "Original A")
        selected_trope_id = client.get(f"/api/stories/{selected_story_id}/tropes").json()["items"][0]["id"]

        response = client.post(
            "/api/exploration/network",
            json={
                "selected_trope_id": selected_trope_id,
                "min_similarity": 0.6,
                "related_limit": 10,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"]["text"] == "first trope"
    assert body["selected_trope_candidates"] == []
    assert len(body["related_tropes"]) >= 2

    assert len(body["original_markers"]) == 3
    assert body["missing_original_coords"] == 1
    original_missing = next(item for item in body["original_markers"] if item["title"] == "Original Missing")
    assert original_missing["has_location"] is False
    assert original_missing["coordinates"] is None
    assert original_missing["abstract"] == "Fallback summary"
    overlap_original = next(item for item in body["original_markers"] if item["title"] == "Overlap")
    assert [item["text"] for item in overlap_original["story_tropes"]] == ["first trope", "first trope variant"]
    assert [item["story_count"] for item in overlap_original["story_tropes"]] == [3, 3]

    assert len(body["related_markers"]) == 3
    assert body["missing_related_coords"] == 1
    related_b = next(item for item in body["related_markers"] if item["title"] == "Related B")
    assert [item["text"] for item in related_b["matched_tropes"]] == ["first echo trope", "first trope variant"]
    assert [item["story_count"] for item in related_b["matched_tropes"]] == [2, 3]
    assert related_b["similarity"] == 1.0

    related_missing = next(item for item in body["related_markers"] if item["title"] == "Related Missing")
    assert related_missing["has_location"] is False
    assert related_missing["coordinates"] is None

    visible_related_ids = {item["story_id"] for item in body["related_markers"]}
    overlap_story_id = next(item["id"] for item in stories if item["title"] == "Overlap")
    assert overlap_story_id not in visible_related_ids

    assert len(body["connections"]) == 2
    assert body["bounds"] is not None


def test_exploration_network_requires_query_or_selected_trope_id(tmp_path) -> None:
    with build_client(tmp_path, "exploration-validation.db") as client:
        response = client.post(
            "/api/exploration/network",
            json={},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "exploration_invalid"
    assert "selected_trope_id or a query" in response.json()["message"]
