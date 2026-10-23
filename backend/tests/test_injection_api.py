import pytest
from sqlalchemy import select

from app.db.models import Dataset, DatasetStatus, Story
from tests.test_dataset_api import client, make_csv_bytes  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_snapshot_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def activate(client):
    client.post("/api/dataset/upload", files={"file": ("initial.csv", make_csv_bytes([{"Story title (Eng)": "Original", "Abstract (Eng)": "Original account"}]), "text/csv")})
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
