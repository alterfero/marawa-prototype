import pytest

from app.db.models import Keyword, ReviewItem, ReviewStatus, TermReviewStatus, Trope
from tests.test_review_api import theme_review_clients  # noqa: F401
from tests.test_story_review_queue_api import create_story


def term_reviews(admin, story, kind):
    reviews = admin.get("/api/review/items").json()
    assignment = next(item for item in reviews if item["subject_id"] == story["id"]
                      and item["metadata"].get("change_kind") == f"story_{kind}")
    canonical = next(item for item in reviews if item["subject_id"] == story[f"{kind}s"][0]["id"]
                     and item["subject_table"] == f"{kind}s")
    return assignment, canonical


def decide(admin, story, review, decision, edit=None, version=None):
    return admin.post(f"/api/review/stories/{story['id']}/items/{review['id']}/{decision}", json={
        "expected_story_version": story["version"] if version is None else version,
        **({"edit": edit} if edit is not None else {}),
    })


@pytest.mark.parametrize("kind", ["trope", "keyword"])
@pytest.mark.parametrize("decision", ["approve", "edit", "reject"])
@pytest.mark.parametrize("shared", [False, True])
def test_story_term_has_one_decision_with_catalogue_resolution(theme_review_clients, kind, decision, shared):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, canonical = term_reviews(admin, story, kind)
    original = story[f"{kind}s"][0]
    if shared:
        other = next(item for item in admin.get("/api/stories").json()["items"] if item["territory"] == "Moorea")
        assert admin.post(f"/api/stories/{other['id']}/{kind}s", json={
            "expected_story_version": other["version"], f"{kind}_id": original["id"],
        }).status_code == 201
    before = admin.get(f"/api/review/stories/{story['id']}/items").json()
    assert assignment["id"] in {item["id"] for item in before}
    assert canonical["id"] not in {item["id"] for item in before}
    result = decide(admin, story, assignment, "reject" if decision == "reject" else "approve",
                    {"text": "Admin replacement"} if decision == "edit" else None)
    assert result.status_code == 200, result.text
    saved = admin.get(f"/api/stories/{story['id']}").json()
    expected = [] if decision == "reject" else ["Admin replacement" if decision == "edit" else original["text"]]
    assert [term["text"] for term in saved[f"{kind}s"]] == expected
    remaining = admin.get(f"/api/review/stories/{story['id']}/items").json()
    assert not {assignment["id"], canonical["id"]} & {item["id"] for item in remaining}
    catalog_state = admin.get(f"/api/review/items/{canonical['id']}").json()
    assert catalog_state["status"] == ("approved" if decision == "approve" else "pending" if shared else "rejected")
    if shared:
        other_saved = admin.get(f"/api/stories/{other['id']}").json()
        assert [term["id"] for term in other_saved[f"{kind}s"]] == [original["id"]]
        other_reviews = admin.get(f"/api/review/stories/{other['id']}/items").json()
        assert (canonical["id"] in {item["id"] for item in other_reviews}) == (decision != "approve")
    history = admin.get(f"/api/review/stories/{story['id']}/items?status=all").json()
    assert sum(item["id"] in {assignment["id"], canonical["id"]} for item in history) == 1
    if decision == "edit":
        assert result.json()["metadata"]["resolution"]["edited_term_id"] == saved[f"{kind}s"][0]["id"]


@pytest.mark.parametrize("kind", ["trope", "keyword"])
@pytest.mark.parametrize("decision", ["approve", "edit", "reject"])
def test_older_catalogue_only_review_is_editable_in_story_context(theme_review_clients, kind, decision):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, canonical = term_reviews(admin, story, kind)
    # Existing deployments may have resolved the assignment but left its catalogue review pending.
    assert admin.post(f"/api/review/items/{assignment['id']}/approve", json={}).status_code == 200
    visible = admin.get(f"/api/review/stories/{story['id']}/items?status=all").json()
    assert canonical["id"] in {item["id"] for item in visible}
    assert assignment["id"] not in {item["id"] for item in visible}
    response = decide(admin, story, canonical, "reject" if decision == "reject" else "approve",
                      {"text": "Updated older proposal"} if decision == "edit" else None)
    assert response.status_code == 200, response.text
    assert response.json()["subject_table"] == "stories"
    saved = admin.get(f"/api/stories/{story['id']}").json()
    expected = [] if decision == "reject" else ["Updated older proposal" if decision == "edit" else story[f"{kind}s"][0]["text"]]
    assert [term["text"] for term in saved[f"{kind}s"]] == expected
    assert canonical["id"] not in {item["id"] for item in admin.get(f"/api/review/stories/{story['id']}/items").json()}


@pytest.mark.parametrize("kind", ["trope", "keyword"])
def test_story_term_decision_is_atomic_and_checks_version(theme_review_clients, monkeypatch, kind):
    from app.services import reviews

    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, canonical = term_reviews(admin, story, kind)
    assert decide(admin, story, assignment, "approve", {"text": "Changed"}, version=99).status_code == 409
    before = admin.get(f"/api/stories/{story['id']}").json()

    def fail_catalogue_update(*args, **kwargs):
        raise RuntimeError("Simulated catalogue failure")

    monkeypatch.setattr(reviews, "_settle_story_term_catalogue_review", fail_catalogue_update)
    with pytest.raises(RuntimeError, match="Simulated catalogue failure"):
        decide(admin, story, assignment, "approve", {"text": "Changed"})
    saved = admin.get(f"/api/stories/{story['id']}").json()
    assert saved == before
    with admin.app.state.session_factory() as session:
        assert session.get(ReviewItem, assignment["id"]).status == ReviewStatus.PENDING
        assert session.get(ReviewItem, canonical["id"]).status == ReviewStatus.PENDING
        assert session.get(Trope if kind == "trope" else Keyword, story[f"{kind}s"][0]["id"]).review_status == TermReviewStatus.PENDING_REVIEW


@pytest.mark.parametrize("kind", ["trope", "keyword"])
def test_unmodified_typed_term_approves_once_and_enforces_story_scope(theme_review_clients, kind):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, canonical = term_reviews(admin, story, kind)
    other = next(item for item in admin.get("/api/stories").json()["items"] if item["territory"] == "Moorea")
    assert decide(contributor, story, assignment, "approve").status_code == 403
    assert decide(admin, other, assignment, "approve").status_code == 400
    assert decide(admin, other, canonical, "approve").status_code == 409
    response = decide(admin, story, assignment, "approve", {"text": story[f"{kind}s"][0]["text"]})
    assert response.status_code == 200, response.text
    assert admin.get(f"/api/review/items/{canonical['id']}").json()["status"] == "approved"
    assert decide(admin, story, assignment, "approve").status_code == 409


@pytest.mark.parametrize("kind", ["trope", "keyword"])
def test_selecting_an_existing_pending_term_resolves_its_catalogue_review(theme_review_clients, kind):
    admin, contributor = theme_review_clients
    story = create_story(contributor)
    assignment, source_catalogue = term_reviews(admin, story, kind)
    target_story = contributor.post("/api/stories", json={
        "expected_dataset_version": contributor.get("/api/dataset/status").json()["active_dataset_version"],
        "fields": {"Story title (Eng)": "Another proposal", "territory": "Tahiti"},
        f"{kind}s": ["Another proposed term"],
    }).json()["story"]
    target_assignment, target_catalogue = term_reviews(admin, target_story, kind)
    response = decide(admin, story, assignment, "approve", {
        "text": "Another proposed term", "existing_term_id": target_story[f"{kind}s"][0]["id"],
    })
    assert response.status_code == 200, response.text
    assert admin.get(f"/api/review/items/{source_catalogue['id']}").json()["status"] == "rejected"
    assert admin.get(f"/api/review/items/{target_catalogue['id']}").json()["status"] == "approved"
    assert admin.get(f"/api/review/items/{target_assignment['id']}").json()["status"] == "pending"
    visible = admin.get(f"/api/review/stories/{story['id']}/items").json()
    assert not {assignment["id"], source_catalogue["id"], target_catalogue["id"]} & {item["id"] for item in visible}
