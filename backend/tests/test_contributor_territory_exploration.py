from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, TROPE_FIELD
from app.db import Dataset, DatasetStatus, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env, redeem_token
from tests.search_fakes import FakeEmbeddingBackend


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def _build_app(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'territory-exploration.db'}")
    session_factory = build_session_factory(engine)
    return create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _row(*, title: str, territory: str, tropes: str = "", coordinates: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row["territory"] = territory
    row[TROPE_FIELD] = tropes
    row["space coord"] = coordinates
    return row


def _upload_active_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", _csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201
    with client.app.state.session_factory() as session:
        staged_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.STAGED))
        assert staged_dataset is not None
        staged_dataset.status = DatasetStatus.ACTIVE
        session.commit()


@pytest.mark.parametrize("territories", [[], ["Tahiti"]])
def test_authenticated_contributor_exploration_includes_all_territories(monkeypatch, tmp_path, territories) -> None:
    configure_auth_env(monkeypatch)
    app = _build_app(tmp_path)

    with TestClient(app) as admin_client, TestClient(app) as contributor_client, TestClient(app) as anonymous_client:
        authenticate_admin(admin_client)
        _upload_active_dataset(
            admin_client,
            [
                _row(title="Tahiti Story", territory="Tahiti", tropes="§§ Shared Trope"),
                _row(title="Moorea Story", territory="Moorea", tropes="§§ Shared Trope"),
            ],
        )
        invite_response = admin_client.post(
            "/api/admin/users",
            json={
                "email": "contributor@example.com",
                "display_name": "Scoped Contributor",
                "role": "contributor",
                "territories": territories,
            },
        )
        assert invite_response.status_code == 201
        redeem_token(
            contributor_client,
            token=invite_response.json()["invite_token"],
            new_password="contributor-password",
        )

        payload = {
            "story_filters": [
                {
                    "field": "territory",
                    "selected_values": ["Tahiti", "Moorea"],
                }
            ]
        }
        contributor_response = contributor_client.post("/api/exploration/network", json=payload)
        candidate_response = contributor_client.post(
            "/api/exploration/network",
            json={"query": "Shared Trope"},
        )
        anonymous_response = anonymous_client.post("/api/exploration/network", json=payload)

        trope_id = candidate_response.json()["selected_trope_candidates"][0]["id"]
        map_response = contributor_client.post("/api/exploration/network", json={"selected_trope_id": trope_id})
        anonymous_map = anonymous_client.post("/api/exploration/network", json={"selected_trope_id": trope_id})

    assert contributor_response.status_code == 200
    assert contributor_response.json() == anonymous_response.json()
    assert map_response.status_code == 200
    assert map_response.json() == anonymous_map.json()
    assert candidate_response.status_code == 200
    candidates = candidate_response.json()["selected_trope_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["text"] == "Shared Trope"
    assert candidates[0]["story_count"] == 2
    assert anonymous_response.status_code == 200
    assert {marker["title"] for marker in anonymous_response.json()["original_markers"]} == {
        "Tahiti Story",
        "Moorea Story",
    }


@pytest.mark.parametrize("territories", [[], ["Tahiti"]])
def test_contributor_trope_sequence_graph_includes_all_territories(monkeypatch, tmp_path, territories) -> None:
    configure_auth_env(monkeypatch)
    app = _build_app(tmp_path)

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        _upload_active_dataset(
            admin_client,
            [
                _row(
                    title="Tahiti Story",
                    territory="Tahiti",
                    tropes="§§ Shared Trope",
                    coordinates="-17.6000, -149.4000",
                ),
                _row(
                    title="Moorea Story",
                    territory="Moorea",
                    tropes="§§ Shared Trope",
                    coordinates="-17.5000, -149.8000",
                ),
            ],
        )
        tahiti_story = next(story for story in admin_client.get("/api/stories").json()["items"] if story["territory"] == "Tahiti")
        trope_id = admin_client.get(f"/api/stories/{tahiti_story['id']}/tropes").json()["items"][0]["id"]
        invite_response = admin_client.post(
            "/api/admin/users",
            json={
                "email": "graph-contributor@example.com",
                "display_name": "Scoped Contributor",
                "role": "contributor",
                "territories": territories,
            },
        )
        assert invite_response.status_code == 201
        redeem_token(
            contributor_client,
            token=invite_response.json()["invite_token"],
            new_password="contributor-password",
        )

        response = contributor_client.post(
            "/api/visualizations/trope-sequence-graph",
            json={
                "selected_trope_id": trope_id,
                "max_stories": 10,
            },
        )

    assert response.status_code == 200
    assert {node["story_title"] for node in response.json()["nodes"]} == {"Tahiti Story", "Moorea Story"}
