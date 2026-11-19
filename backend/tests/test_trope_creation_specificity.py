import pytest
from fastapi.testclient import TestClient

from app.compute.specificity import classify
from app.db import build_engine, build_session_factory
from app.db.models import TermReviewStatus, Trope, UserRole
from app.main import create_app
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env
from tests.test_tropes_api import make_row, upload_dataset


@pytest.fixture
def clients(monkeypatch, tmp_path):
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'specificity.db'}")
    app = create_app(db_engine=engine, session_factory=build_session_factory(engine), job_runner_enabled=False)
    with TestClient(app) as admin, TestClient(app) as contributor:
        authenticate_admin(admin)
        row = make_row(title="Story", tropes="§§ Original trope")
        row["territory"] = "Tahiti"
        upload_dataset(admin, [row])
        authenticate_role(admin, contributor, email="contributor@example.com", display_name="Contributor",
                          role=UserRole.CONTRIBUTOR, password="contributor-password", territories=["Tahiti"])
        yield admin, contributor


def create_trope(client, method, text, level):
    if method == "catalogue":
        response = client.post("/api/tropes", json={"text": text, "specificity_level": level})
    elif method == "story":
        status = client.get("/api/dataset/status").json()
        response = client.post("/api/stories", json={
            "expected_dataset_version": status["active_dataset_version"],
            "fields": {"Story title (Eng)": "New story", "territory": "Tahiti"},
            "tropes": [text], "trope_specificity_levels": {text: level},
        })
    else:
        story = client.get("/api/stories").json()["items"][0]
        payload = {"expected_story_version": story["version"], "text": text, "specificity_level": level}
        url = f"/api/stories/{story['id']}/tropes"
        if method == "replace":
            trope = client.get(f"/api/stories/{story['id']}").json()["tropes"][0]
            response = client.put(f"{url}/{trope['id']}", json=payload)
        else:
            response = client.post(url, json=payload)
    return response


@pytest.mark.parametrize("role", ["admin", "contributor"])
@pytest.mark.parametrize("method", ["catalogue", "story", "add", "replace"])
def test_creation_preserves_selected_specificity(clients, role, method):
    admin, contributor = clients
    client = admin if role == "admin" else contributor
    for level in range(1, 5):
        text = f"A traveler chooses the path numbered {level}"
        response = create_trope(client, method, text, level)
        assert response.status_code in (200, 201), response.text
        trope = next(t for t in admin.get("/api/tropes").json() if t["text"] == text)
        detail = admin.get(f"/api/tropes/{trope['id']}").json()
        assert (detail["specificity_level"], detail["specificity_source"]) == (level, "curator")
        with admin.app.state.session_factory() as session:
            stored = session.get(Trope, trope["id"])
            assert stored.review_status == (TermReviewStatus.APPROVED if role == "admin" else TermReviewStatus.PENDING_REVIEW)


@pytest.mark.parametrize("method", ["catalogue", "story", "add", "replace"])
def test_creation_rejects_invalid_specificity(clients, method):
    admin, _ = clients
    before = admin.get("/api/tropes").json()
    for level in (0, 5, 1.5, True, "2"):
        response = create_trope(admin, method, "Invalid new trope", level)
        assert response.status_code == 422, response.text
    assert admin.get("/api/tropes").json() == before


@pytest.mark.parametrize("method", ["catalogue", "story", "add", "replace"])
def test_reusing_trope_does_not_change_specificity(clients, method):
    admin, contributor = clients
    existing = admin.post("/api/tropes", json={"text": "Existing trope", "specificity_level": 2}).json()["trope"]
    response = create_trope(contributor, method, "  EXISTING trope  ", 4)
    assert response.status_code in (200, 201), response.text
    after = admin.get(f"/api/tropes/{existing['id']}").json()
    assert (after["specificity_level"], after["specificity_source"], after["version"]) == (2, "curator", 1)


def test_omitting_specificity_keeps_automatic_rating(clients):
    admin, _ = clients
    text = "A traveler follows the moon across the lagoon"
    response = admin.post("/api/tropes", json={"text": text})
    assert response.status_code == 200
    trope = response.json()["trope"]
    assert (trope["specificity_level"], trope["specificity_source"]) == classify(text)
