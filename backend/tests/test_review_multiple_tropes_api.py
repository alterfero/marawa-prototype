import csv
import io

import pytest

from app.core.csv_schema import CSV_COLUMNS, TROPE_FIELD
from app.db.models import Dataset, DatasetStatus, Trope
from tests.test_review_api import theme_review_clients  # noqa: F401
from tests.test_story_review_queue_api import create_story
from tests.test_story_term_decision_api import term_reviews


def make_targets(admin):
    targets = []
    for text in ["A hero crosses the ocean", "A bird guides the journey"]:
        response = admin.post("/api/tropes", json={"text": text})
        assert response.status_code == 200
        targets.append(response.json()["trope"])
    return targets


def select_tropes(admin, story, review, ids, **options):
    return admin.post(f"/api/review/stories/{story['id']}/items/{review['id']}/approve", json={
        "expected_story_version": story["version"], "existing_trope_ids": ids, **options,
    })


@pytest.mark.parametrize("keep_proposed", [False, True])
@pytest.mark.parametrize("catalogue_only", [False, True])
def test_review_replaces_or_augments_with_multiple_tropes(theme_review_clients, keep_proposed, catalogue_only):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, catalogue = term_reviews(admin, story, "trope")
    original = story["tropes"][0]
    targets = make_targets(admin)
    if catalogue_only:
        assert admin.post(f"/api/review/items/{assignment['id']}/approve", json={}).status_code == 200
    response = select_tropes(admin, story, catalogue if catalogue_only else assignment,
                             [term["id"] for term in targets], keep_proposed_trope=keep_proposed)
    assert response.status_code == 200, response.text
    saved = admin.get(f"/api/stories/{story['id']}").json()
    expected = ([original] if keep_proposed else []) + targets
    assert [term["id"] for term in saved["tropes"]] == [term["id"] for term in expected]
    assert saved["version"] > story["version"]
    resolution = response.json()["metadata"]["resolution"]
    assert resolution["decision"] == "edited"
    assert resolution["edited_term_ids"] == [term["id"] for term in expected]
    assert resolution["edited_term_texts"] == [term["text"] for term in expected]
    assert resolution["keep_proposed_trope"] == keep_proposed
    assert admin.get(f"/api/review/items/{catalogue['id']}").json()["status"] == ("approved" if keep_proposed else "rejected")
    pending = admin.get(f"/api/review/stories/{story['id']}/items").json()
    assert not any(item["metadata"].get("change_kind") == "story_trope" for item in pending)
    rows = csv.DictReader(io.StringIO(admin.get("/api/dataset/export.csv").content.decode("utf-8-sig")))
    assert rows.fieldnames == CSV_COLUMNS
    row = next(row for row in rows if row["Story title (Eng)"] == story["fields"]["Story title (Eng)"])
    assert row[TROPE_FIELD] == "\n".join(f"§§ {term['text']}" for term in expected)


@pytest.mark.parametrize("all_assigned", [False, True])
def test_selection_reuses_assignments_and_preserves_shared_proposal(theme_review_clients, all_assigned):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, catalogue = term_reviews(admin, story, "trope")
    original = story["tropes"][0]
    targets = make_targets(admin)
    other = next(item for item in admin.get("/api/stories").json()["items"] if item["territory"] == "Moorea")
    assert admin.post(f"/api/stories/{other['id']}/tropes", json={
        "expected_story_version": other["version"], "trope_id": original["id"],
    }).status_code == 201
    other_before = admin.get(f"/api/stories/{other['id']}").json()
    for target in targets if all_assigned else targets[:1]:
        added = admin.post(f"/api/stories/{story['id']}/tropes", json={
            "expected_story_version": story["version"], "trope_id": target["id"],
        })
        assert added.status_code == 201
        story = admin.get(f"/api/stories/{story['id']}").json()
    response = select_tropes(admin, story, assignment, [term["id"] for term in targets])
    assert response.status_code == 200, response.text
    saved = admin.get(f"/api/stories/{story['id']}").json()
    assert len(saved["tropes"]) == 2
    assert {term["id"] for term in saved["tropes"]} == {term["id"] for term in targets}
    other_saved = admin.get(f"/api/stories/{other['id']}").json()
    assert other_saved["tropes"][0]["story_count"] == 1
    other_before["tropes"][0]["story_count"] = 1
    assert other_saved == other_before
    assert admin.get(f"/api/review/items/{catalogue['id']}").json()["status"] == "pending"


@pytest.mark.parametrize("failure", ["stale", "missing", "other_dataset", "duplicate", "empty", "edit", "keyword", "catalogue_failure"])
def test_multiple_trope_decision_is_atomic_and_validated(theme_review_clients, monkeypatch, failure):
    from app.services import reviews

    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, catalogue = term_reviews(admin, story, "trope")
    targets = make_targets(admin)
    ids = [term["id"] for term in targets]
    options = {}
    if failure == "stale":
        options["expected_story_version"] = story["version"] + 1
    elif failure == "missing":
        ids.append("missing-trope")
    elif failure == "other_dataset":
        with admin.app.state.session_factory() as session:
            dataset = Dataset(source_filename="other.csv", status=DatasetStatus.ARCHIVED)
            session.add(dataset)
            session.flush()
            trope = Trope(dataset_id=dataset.id, text="Foreign", normalized_text="foreign")
            session.add(trope)
            session.commit()
            ids.append(trope.id)
    elif failure == "duplicate":
        ids.append(ids[0])
    elif failure == "empty":
        ids = []
    elif failure == "edit":
        options["edit"] = {"text": "Also typed"}
    elif failure == "keyword":
        assignment, _ = term_reviews(admin, story, "keyword")
    elif failure == "catalogue_failure":
        def fail_catalogue(*args, **kwargs):
            raise RuntimeError("Simulated catalogue failure")
        monkeypatch.setattr(reviews, "_settle_story_term_catalogue_review", fail_catalogue)
    before = admin.get(f"/api/stories/{story['id']}").json()
    if failure == "catalogue_failure":
        with pytest.raises(RuntimeError, match="Simulated catalogue failure"):
            select_tropes(admin, story, assignment, ids, **options)
    else:
        response = select_tropes(admin, story, assignment, ids, **options)
        assert response.status_code == (409 if failure == "stale" else 422 if failure == "empty" else 400), response.text
    assert admin.get(f"/api/stories/{story['id']}").json() == before
    assert admin.get(f"/api/review/items/{assignment['id']}").json()["status"] == "pending"
    assert admin.get(f"/api/review/items/{catalogue['id']}").json()["status"] == "pending"
