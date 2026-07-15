import csv
import io
from collections import Counter

from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.core.projection import project_lon_lat_equirectangular
from app.db import StoryTrope, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(
    *,
    title: str,
    tropes: str = "",
    keywords: str = "",
    coord: str = "",
    abstract: str = "",
) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    row["space coord"] = coord
    row["Abstract (Eng)"] = abstract
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


def request_rebuild(client: TestClient) -> None:
    response = client.post("/api/dataset/rebuild")
    assert response.status_code == 200


def process_next_job(client: TestClient) -> None:
    assert client.app.state.job_runner.process_next_job() is True


def test_projection_is_deterministic() -> None:
    point_a = project_lon_lat_equirectangular(165.25, -20.85)
    point_b = project_lon_lat_equirectangular(165.25, -20.85)

    assert point_a == point_b
    assert point_a.x == 1322.0
    assert point_a.y == 166.8


def test_trope_sequence_graph_caps_max_stories(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "trope-graph-cap.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story 1", tropes="§§ first trope", coord="-20.0, 165.0"),
                make_row(title="Story 2", tropes="§§ first trope", coord="-21.0, 166.0"),
                make_row(title="Story 3", tropes="§§ first trope", coord="-22.0, 167.0"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        response = client.post(
            "/api/visualizations/trope-sequence-graph",
            json={
                "query": "first trope",
                "max_stories": 2,
                "max_links_per_node": 1,
                "vertical_spacing": 24,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["layout_basis"]["selected_trope"]["text"] == "first trope"
    assert body["layout_basis"]["max_stories"] == 2
    assert len({node["story_id"] for node in body["nodes"]}) == 2
    assert len([node for node in body["nodes"] if node["kind"] == "story_anchor"]) == 2
    assert any("Capped the graph to 2 stories" in warning for warning in body["warnings"])


def test_trope_sequence_graph_semantic_links_respect_similarity_threshold(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "trope-graph-threshold.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(
                    title="Selected",
                    tropes="§§ first trope\n§§ second trope",
                    coord="-20.0, 165.0",
                ),
                make_row(
                    title="Similar",
                    tropes="§§ first trope variant\n§§ third trope",
                    coord="-20.5, 165.5",
                ),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        response = client.post(
            "/api/visualizations/trope-sequence-graph",
            json={
                "query": "first trope",
                "similarity_threshold": 0.95,
                "max_stories": 10,
                "max_links_per_node": 4,
                "vertical_spacing": 24,
            },
        )

    assert response.status_code == 200
    body = response.json()
    semantic_links = [link for link in body["links"] if link["kind"] == "semantic"]
    assert semantic_links
    trope_text_by_node_id = {
        node["id"]: node.get("trope_text")
        for node in body["nodes"]
        if node["kind"] == "trope_occurrence"
    }
    assert {
        trope_text_by_node_id[semantic_links[0]["source"]],
        trope_text_by_node_id[semantic_links[0]["target"]],
    } == {"first trope", "first trope variant"}
    assert all(link["similarity"] >= 0.95 for link in semantic_links)
    semantic_texts = {
        trope_text_by_node_id[link["source"]]
        for link in semantic_links
    } | {
        trope_text_by_node_id[link["target"]]
        for link in semantic_links
    }
    assert "second trope" not in semantic_texts
    assert "third trope" not in semantic_texts


def test_trope_sequence_graph_enforces_max_links_per_node(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "trope-graph-max-links.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story 1", tropes="§§ first trope", coord="-20.0, 165.0"),
                make_row(title="Story 2", tropes="§§ first trope", coord="-21.0, 166.0"),
                make_row(title="Story 3", tropes="§§ first trope", coord="-22.0, 167.0"),
                make_row(title="Story 4", tropes="§§ first trope", coord="-23.0, 168.0"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        response = client.post(
            "/api/visualizations/trope-sequence-graph",
            json={
                "query": "first trope",
                "similarity_threshold": 0.8,
                "max_stories": 10,
                "max_links_per_node": 1,
                "vertical_spacing": 24,
            },
        )

    assert response.status_code == 200
    body = response.json()
    semantic_links = [link for link in body["links"] if link["kind"] == "semantic"]
    degrees = Counter()
    for link in semantic_links:
        degrees[link["source"]] += 1
        degrees[link["target"]] += 1
    assert semantic_links
    assert max(degrees.values()) == 1


def test_trope_sequence_graph_excludes_invalid_coordinates_with_warning(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "trope-graph-invalid-coords.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Valid Story", tropes="§§ first trope", coord="-20.0, 165.0"),
                make_row(title="Missing Story", tropes="§§ first trope variant", coord="unknown"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        response = client.post(
            "/api/visualizations/trope-sequence-graph",
            json={
                "query": "first trope",
                "similarity_threshold": 0.8,
                "max_stories": 10,
                "max_links_per_node": 2,
                "vertical_spacing": 24,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert len({node["story_id"] for node in body["nodes"]}) == 1
    assert any("Excluded 1 selected stories without valid coordinates" in warning for warning in body["warnings"])


def test_trope_sequence_graph_labels_sequence_axis_as_assignment_order_when_positions_are_missing(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "trope-graph-sequence-label.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(
                    title="Ordered Story",
                    tropes="§§ first trope\n§§ second trope",
                    coord="-20.0, 165.0",
                ),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        with client.app.state.session_factory() as session:
            for link in session.query(StoryTrope).all():
                link.position = None
            session.commit()

        response = client.post(
            "/api/visualizations/trope-sequence-graph",
            json={
                "query": "first trope",
                "max_stories": 10,
                "max_links_per_node": 2,
                "vertical_spacing": 24,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["layout_basis"]["sequence_axis_label"] == "assignment order"
    occurrence_nodes = [node for node in body["nodes"] if node["kind"] == "trope_occurrence"]
    assert [node["sequence_index"] for node in occurrence_nodes] == [0, 1]
