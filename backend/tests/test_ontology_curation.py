import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import TROPE_FIELD
from app.core.ontology import validate_graph
from app.db import Trope, build_engine, build_session_factory
from app.db.models import TropeParent
from app.main import create_app
from app.services.csv_io import export_active_dataset_to_csv_bytes, import_csv_bytes
from app.services.ontology import populate_ontology, read_graph
from tests.auth_helpers import authenticate_admin, configure_auth_env
from tests.test_csv_io import activate_dataset, make_csv_bytes, make_session
from tests.test_tropes_api import make_row, upload_dataset


def test_ontology_edit_checks_version_scope_and_cycles(monkeypatch, tmp_path):
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'edit.db'}")
    app = create_app(db_engine=engine, session_factory=build_session_factory(engine), job_runner_enabled=False)
    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="S", tropes="§§ an animal turns into a man\n§§ a snake turns into a man")])
        graph = client.get("/api/ontology").json()
        child, parent = sorted(graph["tropes"], key=lambda t: -t["specificity_level"])
        assert graph["root"]["specificity_level"] == 0
        payload = {"dataset_id": graph["dataset_id"], "expected_version": graph["version"],
                   "child_id": child["id"], "parent_id": parent["id"], "status": "approved"}
        assert client.put("/api/ontology/link", json=payload).status_code == 200
        assert client.put("/api/ontology/link", json=payload).status_code == 409
        graph = client.get("/api/ontology").json()
        assert graph["root_children"] == [parent["id"]]
        level = {"dataset_id": graph["dataset_id"], "expected_version": graph["version"], "trope_id": child["id"]}
        assert client.put("/api/ontology/level", json={**level, "level": 0}).status_code == 422
        assert client.put("/api/ontology/level", json={**level, "level": 1}).status_code == 422
        assert client.put("/api/ontology/level", json={**level, "level": parent["specificity_level"]}).status_code == 200
        payload.update(expected_version=graph["version"] + 1, child_id=parent["id"], parent_id=child["id"])
        assert client.put("/api/ontology/link", json=payload).status_code == 422
        assert client.get("/api/ontology").json()["version"] == payload["expected_version"]
        assert client.get(f"/api/tropes/{child['id']}").json()["specificity_source"] == "curator"


def test_full_export_restores_ratings_and_graph_decisions(tmp_path):
    with make_session(tmp_path) as session:
        dataset = import_csv_bytes(session, make_csv_bytes([{
            "Story title (Eng)": "The transformation",
            TROPE_FIELD: "§§ a non-human turns into a man\n§§ an animal turns into a man\n§§ a snake turns into a man",
        }]))
        activate_dataset(session, dataset)
        assert populate_ontology(session, dataset.id)["links_added"]["inferred"] == 2
        rows = session.scalars(select(TropeParent).order_by(TropeParent.child_id)).all()
        rows[0].status = "rejected"
        rows[1].status = "pending"
        trope = session.get(Trope, rows[1].child_id)
        trope.specificity_level, trope.specificity_source = 4, "curator"
        session.commit()
        assert read_graph(session)["links"] == []
        assert len(read_graph(session, review=True)["links"]) == 1
        assert not populate_ontology(session, dataset.id)["links_added"]
        def state(dataset_id):
            tropes = session.scalars(select(Trope).where(Trope.dataset_id == dataset_id)).all()
            ids = {t.id: t.ontology_id for t in tropes}
            edges = session.scalars(select(TropeParent).where(TropeParent.dataset_id == dataset_id))
            return ({(t.ontology_id, t.text, t.specificity_level, t.specificity_source) for t in tropes},
                    {(ids[e.child_id], ids[e.parent_id], e.status) for e in edges})
        original = state(dataset.id)
        data = export_active_dataset_to_csv_bytes(session, include_marawa_metadata=True)
        restored = import_csv_bytes(session, data, source_filename="restore.csv")
        assert state(restored.id) == original
        assert not populate_ontology(session, restored.id)["links_added"]


def test_graph_allows_multiple_parents_and_rejects_missing_nodes():
    validate_graph({"c": 4, "p1": 2, "p2": 2}, [("c", "p1", "approved"), ("c", "p2", "approved")])
    with pytest.raises(ValueError, match="two different"):
        validate_graph({"child": 4}, [("child", "missing", "approved")])

def test_map_matches_story_only_after_enabling_its_specificity_band(monkeypatch, tmp_path):
    from tests.test_exploration_api import build_client, process_next_job, request_rebuild
    from tests.test_exploration_api import upload_dataset as stage_dataset
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "scope.db") as client:
        authenticate_admin(client)
        stage_dataset(client, [
            make_row(title="Animal story", tropes="§§ an animal turns into a man"),
            make_row(title="Snake story", tropes="§§ a snake turns into a man"),
        ])
        request_rebuild(client)
        process_next_job(client)
        graph = client.get("/api/ontology").json()
        animal = next(t for t in graph["tropes"] if t["text"].startswith("an animal"))
        for scope, titles in [("same", {"Animal story"}), ("narrower", {"Animal story", "Snake story"})]:
            response = client.post("/api/exploration/network", json={
                "selected_trope_id": animal["id"], "specificity_scope": scope,
            })
            assert response.status_code == 200
            result = response.json()
            assert {m["title"] for m in result["original_markers"]} == titles
            assert not result["related_markers"]
        before = client.get("/api/dataset/status").json()["embedding_status"]
        assert before["current"]
        assert client.put("/api/ontology/level", json={
            "trope_id": animal["id"], "dataset_id": graph["dataset_id"],
            "expected_version": graph["version"], "level": animal["specificity_level"],
        }).status_code == 200
        assert client.get("/api/dataset/status").json()["embedding_status"]["current"]
