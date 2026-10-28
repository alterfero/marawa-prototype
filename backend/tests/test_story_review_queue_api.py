import pytest

from app.core.csv_schema import THEME_FIELD
from app.db.models import Dataset, DatasetStatus, ReviewItem, ReviewStatus, ReviewType
from tests.test_review_api import theme_review_clients  # noqa: F401


def create_story(contributor, title="Story with terms"):
    response = contributor.post("/api/stories", json={
        "expected_dataset_version": contributor.get("/api/dataset/status").json()["active_dataset_version"],
        "fields": {"Story title (Eng)": title, "territory": "Tahiti", THEME_FIELD: "New theme"},
        "tropes": ["The ocean rises"],
        "keywords": ["Flood"],
    })
    assert response.status_code == 201
    return response.json()["story"]


def test_story_queue_includes_all_related_reviews_once(theme_review_clients):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    queue = admin.get("/api/review/stories")
    assert queue.status_code == 200
    assert [item["story_id"] for item in queue.json()] == [story["id"]]
    reviews = admin.get(f"/api/review/stories/{story['id']}/items").json()
    assert {item["subject_table"] for item in reviews} == {"stories"}
    assert len(reviews) == len({item["id"] for item in reviews}) == 5
    assert queue.json()[0]["item_count"] == queue.json()[0]["pending_count"] == 5
    assert queue.json()[0]["title"] == story["fields"]["Story title (Eng)"]
    assert any(item["metadata"].get("field_name") == THEME_FIELD for item in reviews)
    assert contributor.get("/api/review/stories").status_code == 403


@pytest.mark.parametrize("kind", ["trope", "keyword"])
def test_shared_pending_term_keeps_both_stories_in_queue(theme_review_clients, kind):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    other = next(item for item in admin.get("/api/stories").json()["items"]
                 if item["fields"]["Story title (Eng)"] == "Other story")
    response = admin.post(f"/api/stories/{other['id']}/{kind}s", json={
        "expected_story_version": other["version"], f"{kind}_id": story[f"{kind}s"][0]["id"],
    })
    assert response.status_code == 201
    all_reviews = admin.get("/api/review/items").json()
    canonical = next(item for item in all_reviews if item["review_type"] == f"{kind}_pending")
    for item in all_reviews:
        if item["id"] != canonical["id"]:
            assert admin.post(f"/api/review/items/{item['id']}/approve", json={}).status_code == 200

    queue = admin.get("/api/review/stories").json()
    assert {item["story_id"] for item in queue} == {story["id"], other["id"]}
    assert all(item["pending_count"] == item["item_count"] == 1 for item in queue)
    for entry in queue:
        assert [item["id"] for item in admin.get(f"/api/review/stories/{entry['story_id']}/items").json()] == [canonical["id"]]
    assert admin.post(f"/api/review/items/{canonical['id']}/approve", json={}).status_code == 200
    assert admin.get("/api/review/stories").json() == []


@pytest.mark.parametrize("kind", ["trope", "keyword"])
def test_removed_term_is_not_repeated_in_story_review_but_keeps_catalogue_history(theme_review_clients, kind):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    reviews = admin.get("/api/review/items").json()
    assignment = next(item for item in reviews if item["metadata"].get("change_kind") == f"story_{kind}")
    canonical = next(item for item in reviews if item["review_type"] == f"{kind}_pending")
    assert admin.post(f"/api/review/items/{assignment['id']}/reject", json={}).status_code == 200
    pending = admin.get(f"/api/review/stories/{story['id']}/items").json()
    assert canonical["id"] not in {item["id"] for item in pending}
    assert admin.post(f"/api/review/items/{canonical['id']}/reject", json={"remove_from_all_stories": True}).status_code == 200
    rejected = admin.get(f"/api/review/stories/{story['id']}/items?status=rejected").json()
    assert {item["id"] for item in rejected} == {assignment["id"]}
    queue = admin.get("/api/review/stories?status=rejected").json()
    assert queue[0]["story_id"] == story["id"]
    assert queue[0]["item_count"] == 1
    assert admin.get(f"/api/review/items/{canonical['id']}").json()["status"] == "rejected"
    assert queue[0]["pending_count"] == 0


def test_queue_limit_counts_stories_and_excludes_unrelated_terms_and_archived_datasets(theme_review_clients):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    second = create_story(contributor, "Second story")
    with admin.app.state.session_factory() as session:
        session.add(ReviewItem(dataset_id=story["dataset_id"], review_type=ReviewType.TROPE_PENDING,
                               subject_table="tropes", subject_id="unassigned-term",
                               status=ReviewStatus.PENDING, metadata_json={"text": "Unassigned term"}))
        session.commit()
    queue = admin.get("/api/review/stories?limit=1").json()
    assert len(queue) == 1
    assert queue[0]["story_id"] in {story["id"], second["id"]}
    assert queue[0]["item_count"] == 5
    assert len(admin.get(f"/api/review/stories/{queue[0]['story_id']}/items").json()) == 5
    with admin.app.state.session_factory() as session:
        session.get(Dataset, story["dataset_id"]).status = DatasetStatus.ARCHIVED
        session.commit()
    assert admin.get("/api/review/stories?status=all").json() == []
