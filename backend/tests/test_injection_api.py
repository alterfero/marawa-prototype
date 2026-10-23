import json

import pytest
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, FULL_EXPORT_COLUMNS, MARAWA_STORY_METADATA_FIELD, MARAWA_TERM_CATALOG_FIELD, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from app.db.models import Dataset, DatasetStatus, Story, Trope, Keyword, TermReviewStatus
from tests.test_dataset_api import client, make_csv_bytes  # noqa: F401
from tests.test_injection import full_injection_rows


@pytest.fixture(autouse=True)
def isolated_snapshot_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def activate(client, rows=None):
    client.post("/api/dataset/upload", files={"file": ("initial.csv", make_csv_bytes(rows or [{"Story title (Eng)": "Original", "Abstract (Eng)": "Original account"}]), "text/csv")})
    with client.app.state.session_factory() as session:
        session.scalar(select(Dataset)).status = DatasetStatus.ACTIVE
        session.commit()


def test_injection_api_validates_persists_and_applies_once(client):
    activate(client)
    response = client.post("/api/dataset/injections/preview", files={"file": ("new.csv", make_csv_bytes([{"Story title (Eng)": "New"}]), "text/csv")})
    assert response.status_code == 200
    report = response.json()
    assert report["error_count"] == 0
    assert client.get(f'/api/dataset/injections/{report["id"]}').json()["rows"] == report["rows"]
    result = client.post(f'/api/dataset/injections/{report["id"]}/apply', json={"decisions": []})
    assert result.status_code == 200
    assert result.json()["added"] == 1
    retry = client.post(f'/api/dataset/injections/{report["id"]}/apply', json={"decisions": []})
    assert retry.status_code == 200
    assert retry.json() == result.json()
    client.app.state.job_runner.process_next_job()
    assert client.get("/api/dataset/status").json()["story_count"] == 2
    assert client.get("/api/dataset/status").json()["embedding_status"]["current"] is True


def test_invalid_csv_report_is_returned_without_mutation(client):
    activate(client)
    response = client.post("/api/dataset/injections/preview", files={"file": ("bad.csv", b"bad,headers\nx,y\n", "text/csv")})
    assert response.status_code == 200
    report = response.json()
    assert report["error_count"] > 0
    assert client.post(f'/api/dataset/injections/{report["id"]}/apply', json={}).status_code == 400
    assert client.get("/api/dataset/status").json()["story_count"] == 1


def test_preview_needs_active_dataset_and_apply_checks_story_version(client):
    assert client.post("/api/dataset/injections/preview", files={"file": ("new.csv", make_csv_bytes([{"Story title (Eng)": "New"}]), "text/csv")}).status_code == 409
    activate(client)
    report = client.post("/api/dataset/injections/preview", files={"file": ("new.csv", make_csv_bytes([{"Story title (Eng)": "Original", "Abstract (Eng)": "Original account"}]), "text/csv")}).json()
    candidate = report["rows"][0]["candidates"][0]
    with client.app.state.session_factory() as session:
        session.scalar(select(Story)).version += 1
        session.commit()
    response = client.post(f'/api/dataset/injections/{report["id"]}/apply', json={"decisions": [{"row_number": 1, "action": "keep_injected", "target_key": candidate["key"]}]})
    assert response.status_code == 409



def test_api_numbers_title_only_collision_without_duplicate_decision(client):
    activate(client)
    report_response = client.post("/api/dataset/injections/preview", files={"file": (
        "incoming.csv", make_csv_bytes([{"Story title (Eng)": "Original", "Abstract (Eng)": "Different account"}]), "text/csv",
    )})
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["duplicate_count"] == report["warning_count"] == 0
    assert report["rows"][0]["fields"]["Story title (Eng)"] == "Original 1"
    response = client.post(f'/api/dataset/injections/{report["id"]}/apply', json={})
    assert response.status_code == 200
    assert response.json()["added"] == 1
    with client.app.state.session_factory() as session:
        assert {s.fields_json["Story title (Eng)"] for s in session.scalars(select(Story))} == {"Original", "Original 1"}


@pytest.mark.parametrize("full_export", [False, True])
def test_admin_can_review_injected_story_fields_assignments_and_terms(client, full_export):
    activate(client)
    rows = [{
        "Story title (Eng)": "Submitted", "Abstract (Eng)": "New account", "Source": "Book",
        THEME_FIELD: "Ocean", TROPE_FIELD: "canoe journey", KEYWORD_FIELD: "canoe",
    }]
    if full_export:
        rows = full_injection_rows(rows)
        rows[0][MARAWA_STORY_METADATA_FIELD] = json.dumps({
            "schema_version": 1, "completeness": "complete", "source_row_number": 99,
            "trope_assignments": [{"text": "canoe journey", "origin": "human_approved", "status": "validated"}],
        })
        rows[0][MARAWA_TERM_CATALOG_FIELD] = json.dumps({
            "schema_version": 1,
            "tropes": [{"text": "canoe journey", "confirmation_status": "canonical", "review_status": "approved"}],
            "themes": [{"text": "Ocean", "confirmation_status": "canonical"}],
            "keywords": [{"text": "canoe", "confirmation_status": "canonical", "review_status": "approved"}],
        })
    report = client.post("/api/dataset/injections/preview", files={"file": (
        "incoming.csv", make_csv_bytes(rows, fieldnames=FULL_EXPORT_COLUMNS if full_export else CSV_COLUMNS), "text/csv",
    )}).json()
    assert report["error_count"] == report["warning_count"] == 0
    assert client.post(f'/api/dataset/injections/{report["id"]}/apply', json={}).status_code == 200
    client.app.state.job_runner.process_next_job()
    response = client.get("/api/review/items")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 8
    assert all(item["status"] == "pending" and item["created_by_user_id"] for item in items)
    assert all(item["metadata"]["injection_id"] == report["id"] for item in items)
    story_items = [item for item in items if item["subject_table"] == "stories"]
    story_id = story_items[0]["subject_id"]
    assert {item["subject_id"] for item in story_items} == {story_id}
    assert client.get(f"/api/stories/{story_id}").json()["completeness"] == "incomplete"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(Trope)).review_status == TermReviewStatus.PENDING_REVIEW
        assert session.scalar(select(Keyword)).review_status == TermReviewStatus.PENDING_REVIEW
    for item in items:
        metadata = item["metadata"]
        reject = metadata.get("field_name") in {"Source", THEME_FIELD} or metadata.get("change_kind") in {"story_trope", "story_keyword"}
        action = "reject" if reject else "approve"
        resolved = client.post(f'/api/review/items/{item["id"]}/{action}', json={})
        assert resolved.status_code == 200, resolved.text
    assert client.get("/api/review/items").json() == []
    story = client.get(f"/api/stories/{story_id}").json()
    assert story["completeness"] == "incomplete"
    assert story["fields"]["Story title (Eng)"] == "Submitted"
    assert story["fields"]["Source"] == ""
    assert story["themes"] == story["keywords"] == story["tropes"] == []


def test_rejecting_injected_replacement_restores_original_values(client):
    activate(client, [{
        "Story title (Eng)": "Original", "Abstract (Eng)": "Original account", "Source": "Original book",
        TROPE_FIELD: "original trope", KEYWORD_FIELD: "original keyword", THEME_FIELD: "Original theme",
    }])
    with client.app.state.session_factory() as session:
        original = session.scalar(select(Story))
        story_id = original.id
    report = client.post("/api/dataset/injections/preview", files={"file": (
        "replacement.csv", make_csv_bytes([{
            "Story title (Eng)": "Original", "Abstract (Eng)": "Original account", "Source": "Injected book",
            TROPE_FIELD: "injected trope", KEYWORD_FIELD: "injected keyword", THEME_FIELD: "Injected theme",
        }]), "text/csv",
    )}).json()
    result = client.post(f'/api/dataset/injections/{report["id"]}/apply', json={"decisions": [
        {"row_number": 1, "action": "keep_injected", "target_key": f"story:{story_id}"},
    ]})
    assert result.status_code == 200
    client.app.state.job_runner.process_next_job()
    items = [item for item in client.get("/api/review/items").json() if item["subject_table"] == "stories"]
    assert len(items) == 8
    assert all(item["review_type"] == "story_updated" and item["subject_id"] == story_id for item in items)
    for item in items:
        assert client.post(f'/api/review/items/{item["id"]}/reject', json={}).status_code == 200
    story = client.get(f"/api/stories/{story_id}").json()
    assert story["fields"]["Source"] == "Original book"
    assert story["fields"]["Story title (Eng)"] == "Original"
    assert story["completeness"] == "incomplete"
    assert [term["text"] for term in story["tropes"]] == ["original trope"]
    assert [term["text"] for term in story["keywords"]] == ["original keyword"]
    assert [term["text"] for term in story["themes"]] == ["Original theme"]
