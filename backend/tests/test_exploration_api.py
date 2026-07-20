import csv
import io

from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import build_engine, build_session_factory
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
    summary: str = "",
    entered_by: str = "",
    territory: str = "",
) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    row["space coord"] = coord
    row["Abstract (Eng)"] = abstract
    row["1-sentence summary"] = summary
    row["Entered by"] = entered_by
    row["territory"] = territory
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


def test_exploration_network_returns_trope_candidates_when_selected_trope_is_missing(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-candidates.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", tropes="§§ first trope"),
                make_row(title="Story Two", tropes="§§ first trope variant"),
            ],
        )
        request_rebuild(client)
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


def test_exploration_network_hides_staged_dataset_candidates_before_rebuild(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-candidates-fallback.db") as client:
        authenticate_admin(client)
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
    assert body["selected_trope_candidates"] == []


def test_exploration_network_builds_markers_connections_and_bounds(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-network.db") as client:
        authenticate_admin(client)
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
        request_rebuild(client)
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


def test_exploration_network_requires_query_or_selected_trope_id(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-validation.db") as client:
        authenticate_admin(client)
        response = client.post(
            "/api/exploration/network",
            json={},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "exploration_invalid"
    assert "selected_trope_id, a query, story_filters, or story_filter_sets" in response.json()["message"]


def test_exploration_network_builds_filter_only_story_map(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-filter-only.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", coord="-20.0, 165.0", entered_by="Alice", territory="Tahiti", tropes="§§ first trope"),
                make_row(title="Story Two", coord="-19.0, 166.0", entered_by="Bob", territory="Moorea", tropes="§§ second trope"),
                make_row(title="Story Three", coord="missing", entered_by="Alice", territory="Tahiti", tropes="§§ third trope"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        response = client.post(
            "/api/exploration/network",
            json={
                "story_filters": [
                    {"field": "Entered by", "selected_values": [" Alice "]},
                    {"field": "territory", "selected_values": ["Tahiti"]},
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"] is None
    assert body["selected_trope_candidates"] == []
    assert body["related_tropes"] == []
    assert body["related_markers"] == []
    assert body["connections"] == []
    assert [item["title"] for item in body["original_markers"]] == ["Story One", "Story Three"]
    assert body["missing_original_coords"] == 1
    assert body["bounds"] == [[-20.0, 165.0], [-20.0, 165.0]]


def test_exploration_network_filters_selected_trope_intersection(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-filter-intersection.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Original Tahiti", tropes="§§ first trope", coord="-20.0, 165.0", territory="Tahiti"),
                make_row(title="Original Moorea", tropes="§§ first trope", coord="-19.5, 165.5", territory="Moorea"),
                make_row(title="Related Tahiti", tropes="§§ first trope variant", coord="-20.2, 165.2", territory="Tahiti"),
                make_row(title="Related Moorea", tropes="§§ first trope variant", coord="-19.7, 165.7", territory="Moorea"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        selected_story_id = next(item["id"] for item in stories if item["title"] == "Original Tahiti")
        selected_trope_id = client.get(f"/api/stories/{selected_story_id}/tropes").json()["items"][0]["id"]

        response = client.post(
            "/api/exploration/network",
            json={
                "selected_trope_id": selected_trope_id,
                "story_filters": [
                    {"field": "territory", "selected_values": ["Tahiti"]},
                ],
                "min_similarity": 0.6,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"]["text"] == "first trope"
    assert [item["title"] for item in body["original_markers"]] == ["Original Tahiti"]
    assert [item["title"] for item in body["related_markers"]] == ["Related Tahiti"]


def test_exploration_network_returns_empty_intersection_for_selected_trope_filters(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-filter-empty-intersection.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Original Tahiti", tropes="§§ first trope", coord="-20.0, 165.0", territory="Tahiti"),
                make_row(title="Related Tahiti", tropes="§§ first trope variant", coord="-20.2, 165.2", territory="Tahiti"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        selected_story_id = next(item["id"] for item in stories if item["title"] == "Original Tahiti")
        selected_trope_id = client.get(f"/api/stories/{selected_story_id}/tropes").json()["items"][0]["id"]

        response = client.post(
            "/api/exploration/network",
            json={
                "selected_trope_id": selected_trope_id,
                "story_filters": [
                    {"field": "territory", "selected_values": ["Moorea"]},
                ],
                "min_similarity": 0.6,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"]["text"] == "first trope"
    assert body["original_markers"] == []
    assert body["related_markers"] == []
    assert body["connections"] == []


def test_exploration_network_builds_multiple_filter_sets(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-multi-filter-sets.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", coord="-20.0, 165.0", entered_by="Alice", territory="Tahiti", tropes="§§ first trope"),
                make_row(title="Story Two", coord="-19.0, 166.0", entered_by="Bob", territory="Moorea", tropes="§§ second trope"),
                make_row(title="Story Three", coord="missing", entered_by="Alice", territory="Tahiti", tropes="§§ third trope"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        response = client.post(
            "/api/exploration/network",
            json={
                "story_filter_sets": [
                    {
                        "id": "set-1",
                        "label": "Set 1",
                        "color": "#1d4ed8",
                        "filters": [
                            {"field": "Entered by", "selected_values": ["Alice"]},
                            {"field": "territory", "selected_values": ["Tahiti"]},
                        ],
                    },
                    {
                        "id": "set-2",
                        "label": "Set 2",
                        "color": "#d97706",
                        "filters": [
                            {"field": "Entered by", "selected_values": ["Bob"]},
                        ],
                    },
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"] is None
    assert body["original_markers"] == []
    assert body["related_markers"] == []
    assert body["connections"] == []
    assert len(body["filter_set_results"]) == 2

    first_set, second_set = body["filter_set_results"]
    assert first_set["filter_set_label"] == "Set 1"
    assert second_set["filter_set_label"] == "Set 2"
    assert first_set["filters"] == [
        {"field": "Entered by", "selected_values": ["Alice"]},
        {"field": "territory", "selected_values": ["Tahiti"]},
    ]
    assert second_set["filters"] == [{"field": "Entered by", "selected_values": ["Bob"]}]
    assert [item["title"] for item in first_set["original_markers"]] == ["Story One", "Story Three"]
    assert [item["title"] for item in second_set["original_markers"]] == ["Story Two"]
    assert first_set["original_markers"][0]["color"] == "#1d4ed8"
    assert second_set["original_markers"][0]["color"] == "#d97706"
    assert first_set["original_markers"][0]["filter_set_id"] == "set-1"
    assert second_set["original_markers"][0]["filter_set_label"] == "Set 2"
    assert body["bounds"] == [[-20.0, 165.0], [-19.0, 166.0]]


def test_exploration_network_builds_multiple_filter_sets_with_selected_tropes(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-multi-filter-selected-tropes.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", coord="-20.0, 165.0", territory="Tahiti", tropes="§§ first trope"),
                make_row(title="Story Two", coord="-19.0, 166.0", territory="Moorea", tropes="§§ second trope"),
                make_row(title="Story Three", coord="missing", territory="Tahiti", tropes="§§ first trope variant"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        story_one_id = next(item["id"] for item in stories if item["title"] == "Story One")
        story_two_id = next(item["id"] for item in stories if item["title"] == "Story Two")
        story_three_id = next(item["id"] for item in stories if item["title"] == "Story Three")
        first_trope = client.get(f"/api/stories/{story_one_id}/tropes").json()["items"][0]
        second_trope = client.get(f"/api/stories/{story_two_id}/tropes").json()["items"][0]
        first_variant_trope = client.get(f"/api/stories/{story_three_id}/tropes").json()["items"][0]

        response = client.post(
            "/api/exploration/network",
            json={
                "story_filter_sets": [
                    {
                        "id": "set-1",
                        "label": "Set 1",
                        "color": "#1d4ed8",
                        "selected_tropes": [
                            {"id": first_trope["id"], "text": first_trope["text"]},
                            {"id": first_variant_trope["id"], "text": first_variant_trope["text"]},
                        ],
                    },
                    {
                        "id": "set-2",
                        "label": "Set 2",
                        "color": "#d97706",
                        "filters": [{"field": "territory", "selected_values": ["Moorea"]}],
                        "selected_tropes": [{"id": second_trope["id"], "text": second_trope["text"]}],
                    },
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"] is None
    assert body["selected_trope_candidates"] == []
    assert len(body["filter_set_results"]) == 2

    first_set, second_set = body["filter_set_results"]
    assert [item["text"] for item in first_set["selected_tropes"]] == ["first trope", "first trope variant"]
    assert [item["title"] for item in first_set["original_markers"]] == ["Story One", "Story Three"]
    assert first_set["missing_original_coords"] == 1

    assert second_set["filters"] == [{"field": "territory", "selected_values": ["Moorea"]}]
    assert [item["text"] for item in second_set["selected_tropes"]] == ["second trope"]
    assert [item["title"] for item in second_set["original_markers"]] == ["Story Two"]
    assert second_set["original_markers"][0]["filter_set_id"] == "set-2"
    assert body["bounds"] == [[-20.0, 165.0], [-19.0, 166.0]]


def test_exploration_network_builds_selected_trope_results_for_multiple_filter_sets(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-multi-filter-trope.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Original Tahiti", tropes="§§ first trope", coord="-20.0, 165.0", territory="Tahiti"),
                make_row(title="Original Moorea", tropes="§§ first trope", coord="-19.5, 165.5", territory="Moorea"),
                make_row(title="Related Tahiti", tropes="§§ first trope variant", coord="-20.2, 165.2", territory="Tahiti"),
                make_row(title="Related Moorea", tropes="§§ first trope variant", coord="-19.7, 165.7", territory="Moorea"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        selected_story_id = next(item["id"] for item in stories if item["title"] == "Original Tahiti")
        selected_trope_id = client.get(f"/api/stories/{selected_story_id}/tropes").json()["items"][0]["id"]

        response = client.post(
            "/api/exploration/network",
            json={
                "selected_trope_id": selected_trope_id,
                "story_filter_sets": [
                    {
                        "id": "set-1",
                        "label": "Set 1",
                        "color": "#1d4ed8",
                        "filters": [{"field": "territory", "selected_values": ["Tahiti"]}],
                    },
                    {
                        "id": "set-2",
                        "label": "Set 2",
                        "color": "#d97706",
                        "filters": [{"field": "territory", "selected_values": ["Moorea"]}],
                    },
                ],
                "min_similarity": 0.6,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"]["text"] == "first trope"
    assert body["related_tropes"][0]["text"] == "first trope variant"
    assert len(body["filter_set_results"]) == 2

    tahiti_set, moorea_set = body["filter_set_results"]
    assert tahiti_set["filters"] == [{"field": "territory", "selected_values": ["Tahiti"]}]
    assert [item["title"] for item in tahiti_set["original_markers"]] == ["Original Tahiti"]
    assert [item["title"] for item in tahiti_set["related_markers"]] == ["Related Tahiti"]
    assert tahiti_set["connections"][0]["filter_set_id"] == "set-1"
    assert moorea_set["filters"] == [{"field": "territory", "selected_values": ["Moorea"]}]
    assert [item["title"] for item in moorea_set["original_markers"]] == ["Original Moorea"]
    assert [item["title"] for item in moorea_set["related_markers"]] == ["Related Moorea"]
    assert moorea_set["related_markers"][0]["filter_set_label"] == "Set 2"


def test_exploration_network_intersects_filter_set_selected_tropes_with_selected_trope_network(
    monkeypatch,
    tmp_path,
) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "exploration-selected-trope-filter-set-intersection.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Original Tahiti", tropes="§§ first trope", coord="-20.0, 165.0", territory="Tahiti"),
                make_row(title="Original Moorea", tropes="§§ first trope", coord="-19.5, 165.5", territory="Moorea"),
                make_row(
                    title="Related Tahiti",
                    tropes="§§ first trope variant\n§§ support trope",
                    coord="-20.2, 165.2",
                    territory="Tahiti",
                ),
                make_row(title="Related Moorea", tropes="§§ support trope", coord="-19.7, 165.7", territory="Moorea"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        original_tahiti_story_id = next(item["id"] for item in stories if item["title"] == "Original Tahiti")
        original_trope = client.get(f"/api/stories/{original_tahiti_story_id}/tropes").json()["items"][0]
        related_tahiti_story_id = next(item["id"] for item in stories if item["title"] == "Related Tahiti")
        related_trope = client.get(f"/api/stories/{related_tahiti_story_id}/tropes").json()["items"][0]

        response = client.post(
            "/api/exploration/network",
            json={
                "selected_trope_id": original_trope["id"],
                "story_filter_sets": [
                    {
                        "id": "set-1",
                        "label": "Set 1",
                        "color": "#1d4ed8",
                        "selected_tropes": [
                            {"id": original_trope["id"], "text": original_trope["text"]},
                            {"id": related_trope["id"], "text": related_trope["text"]},
                        ],
                        "filters": [{"field": "territory", "selected_values": ["Tahiti"]}],
                    }
                ],
                "min_similarity": 0.6,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_trope"]["text"] == "first trope"
    assert len(body["filter_set_results"]) == 1
    filter_set = body["filter_set_results"][0]
    assert [item["text"] for item in filter_set["selected_tropes"]] == ["first trope", "first trope variant"]
    assert [item["title"] for item in filter_set["original_markers"]] == ["Original Tahiti"]
    assert [item["title"] for item in filter_set["related_markers"]] == ["Related Tahiti"]
