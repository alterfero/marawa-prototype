import csv
import io

import pytest

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from app.db.models import Keyword, TermReviewStatus, Trope
from tests.test_review_api import theme_review_clients  # noqa: F401


@pytest.mark.parametrize("kind,field", [("trope", TROPE_FIELD), ("keyword", KEYWORD_FIELD), ("theme", THEME_FIELD)])
@pytest.mark.parametrize("existing", [False, True])
def test_review_keeps_typed_text_on_selected_story(theme_review_clients, kind, field, existing):
    admin, contributor = theme_review_clients
    created = contributor.post("/api/stories", json={
        "expected_dataset_version": 1,
        "fields": {"Story title (Eng)": "Typed term review", "territory": "Tahiti", THEME_FIELD: "Contributor theme"},
        "tropes": ["Contributor trope"], "keywords": ["Contributor keyword"],
    })
    assert created.status_code == 201
    story = created.json()["story"]
    original = story[f"{kind}s"][0]
    other = next(item for item in admin.get("/api/stories").json()["items"] if item["territory"] == "Moorea")
    # A replacement on this story must not rename or replace a shared contributor term.
    attached = admin.post(f"/api/stories/{other['id']}/{kind}s", json={
        "expected_story_version": other["version"], f"{kind}_id": original["id"],
    })
    assert attached.status_code == 201
    typed = f"The admin’s new {kind}: ocean & sky"
    if existing:
        canonical = admin.post(f"/api/{kind}s", json={"text": typed})
        assert canonical.status_code == 200
        existing_id = canonical.json()[kind]["id"]
    reviews = admin.get("/api/review/items").json()
    review = next(item for item in reviews if (
        item["metadata"].get("field_name") == THEME_FIELD if kind == "theme"
        else item["metadata"].get("change_kind") == f"story_{kind}"
    ))
    response = admin.post(f"/api/review/items/{review['id']}/approve", json={
        "expected_story_version": story["version"], "edit": {"text": typed},
    })
    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["resolution"]["decision"] == "edited"
    saved = admin.get(f"/api/stories/{story['id']}").json()
    replacement = saved[f"{kind}s"][0]
    assert replacement["text"] == typed
    assert replacement["id"] != original["id"]
    if kind != "theme":
        assert response.json()["metadata"]["resolution"]["edited_term_id"] == replacement["id"]
        history = admin.get(f"/api/review/items/{review['id']}").json()
        assert history["metadata"]["resolution"]["edited_term_id"] == replacement["id"]
        assert history["metadata"][f"current_{kind}_id"] == original["id"]
    if existing:
        assert replacement["id"] == existing_id
    else:
        canonical = admin.get(f"/api/{kind}s/{replacement['id']}").json()
        assert canonical["text"] == typed
        if kind != "theme":
            with admin.app.state.session_factory() as session:
                term = session.get(Trope if kind == "trope" else Keyword, replacement["id"])
                assert term.review_status == TermReviewStatus.APPROVED
    untouched = admin.get(f"/api/stories/{other['id']}").json()
    assert any(term["id"] == original["id"] and term["text"] == original["text"] for term in untouched[f"{kind}s"])
    pending = admin.get("/api/review/items").json()
    assert {item["id"] for item in pending} == {item["id"] for item in reviews if item["id"] != review["id"]}
    rows = csv.DictReader(io.StringIO(admin.get("/api/dataset/export.csv").content.decode("utf-8-sig")))
    assert rows.fieldnames == CSV_COLUMNS
    row = next(row for row in rows if row["Story title (Eng)"] == "Typed term review")
    assert typed in row[field]


def test_keep_typed_theme_can_preserve_multiple_theme_entries(theme_review_clients):
    admin, contributor = theme_review_clients
    story = contributor.get("/api/stories").json()["items"][0]
    changed = contributor.patch(f"/api/stories/{story['id']}", json={
        "expected_story_version": story["version"], "fields": {THEME_FIELD: "Contributor theme; Ocean"},
    })
    assert changed.status_code == 200
    review = next(item for item in admin.get("/api/review/items").json() if item["metadata"].get("field_name") == THEME_FIELD)
    response = admin.post(f"/api/review/items/{review['id']}/approve", json={
        "expected_story_version": changed.json()["story"]["version"],
        "edit": {"text": "Admin theme\nOcean\nSecond admin theme"},
    })
    assert response.status_code == 200, response.text
    saved = admin.get(f"/api/stories/{story['id']}").json()
    assert [term["text"] for term in saved["themes"]] == ["Admin theme", "Ocean", "Second admin theme"]
