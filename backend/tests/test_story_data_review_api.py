import csv
import io

import pytest

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from tests.test_review_api import theme_review_clients  # noqa: F401


def propose(contributor, story, fields):
    response = contributor.patch(f"/api/stories/{story['id']}", json={
        "expected_story_version": story["version"], "fields": fields,
    })
    assert response.status_code == 200, response.text
    return response.json()["story"]


def review_payload(admin, story):
    response = admin.get(f"/api/review/stories/{story['id']}/items")
    assert response.status_code == 200, response.text
    items = [item for item in response.json()
             if item["metadata"].get("change_kind") == "story_field"
             and item["metadata"]["field_name"] not in {THEME_FIELD, TROPE_FIELD, KEYWORD_FIELD}]
    return {"expected_story_version": story["version"], "review_ids": [item["id"] for item in items]}


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("creation", [False, True])
def test_story_data_decision_resolves_all_fields_and_keeps_term_reviews(theme_review_clients, decision, creation):
    admin, contributor = theme_review_clients
    fields = {"Story title (Eng)": "Proposed title", "Abstract (Eng)": "Proposed abstract",
              "territory": "Tahiti", "date of recording": "[1900, 1900]", THEME_FIELD: "Proposed theme"}
    if creation:
        response = contributor.post("/api/stories", json={
            "expected_dataset_version": 1, "fields": fields,
            "tropes": ["Proposed trope"], "keywords": ["Proposed keyword"],
        })
        assert response.status_code == 201
        story = response.json()["story"]
    else:
        original = contributor.get("/api/stories").json()["items"][0]
        story = propose(contributor, original, fields)
    payload = review_payload(admin, story)
    before_reviews = admin.get("/api/review/items?status=all").json()
    before_story = admin.get(f"/api/stories/{story['id']}").json()
    if decision == "approve":
        payload["fields"] = {"Story title (Eng)": "Admin title", "Abstract (Eng)": "Admin abstract"}
    payload["note"] = "Reviewed together"
    response = admin.post(f"/api/review/stories/{story['id']}/{decision}", json=payload)
    assert response.status_code == 200, response.text
    resolved = response.json()
    assert {item["id"] for item in resolved} == set(payload["review_ids"])
    assert all(item["status"] == ("approved" if decision == "approve" else "rejected") for item in resolved)
    assert all(item["metadata"]["resolution"]["note"] == "Reviewed together" for item in resolved)
    after = admin.get(f"/api/stories/{story['id']}").json()
    assert after["version"] == story["version"] + 1
    assert after["fields"]["Story title (Eng)"] == ("Admin title" if decision == "approve" else "" if creation else original["fields"]["Story title (Eng)"])
    assert after["fields"]["Abstract (Eng)"] == ("Admin abstract" if decision == "approve" else "")
    assert after["fields"]["date of recording"] == ("[1900, 1900]" if decision == "approve" else "")
    for kind in ("themes", "tropes", "keywords"):
        assert after[kind] == before_story[kind]
    untouched_ids = {item["id"] for item in before_reviews} - set(payload["review_ids"])
    after_reviews = admin.get("/api/review/items?status=all").json()
    assert {item["id"]: (item["status"], item["metadata"]) for item in after_reviews if item["id"] in untouched_ids} == {item["id"]: (item["status"], item["metadata"]) for item in before_reviews if item["id"] in untouched_ids}
    exported = csv.DictReader(io.StringIO(admin.get("/api/dataset/export.csv").content.decode("utf-8-sig")))
    assert exported.fieldnames == CSV_COLUMNS
    assert any(row["Story title (Eng)"] == after["fields"]["Story title (Eng)"] for row in exported)
    with admin.app.state.session_factory() as session:
        from app.db.models import Story
        saved = session.get(Story, story["id"])
        assert saved.recording_year_start == (1900 if decision == "approve" else None)
    assert admin.post(f"/api/review/stories/{story['id']}/{decision}", json=payload).status_code == 409


def test_reject_unwinds_repeated_contributor_edits(theme_review_clients):
    admin, contributor = theme_review_clients
    story = contributor.get("/api/stories").json()["items"][0]
    original = admin.get(f"/api/stories/{story['id']}").json()
    story = propose(contributor, story, {"Abstract (Eng)": "First", "Source": "First source"})
    story = propose(contributor, story, {"Abstract (Eng)": "Second", "Source": "Second source"})
    payload = review_payload(admin, story)
    assert len(payload["review_ids"]) == 4
    response = admin.post(f"/api/review/stories/{story['id']}/reject", json=payload)
    assert response.status_code == 200, response.text
    assert admin.get(f"/api/stories/{story['id']}").json()["fields"] == original["fields"]


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("conflict", ["version", "review_set", "resolved"])
def test_group_decision_rejects_stale_snapshot(theme_review_clients, decision, conflict):
    admin, contributor = theme_review_clients
    story = contributor.get("/api/stories").json()["items"][0]
    story = propose(contributor, story, {"Abstract (Eng)": "First", "Source": "Source"})
    payload = review_payload(admin, story)
    if conflict == "version":
        propose(contributor, story, {"Abstract (Eng)": "Later edit"})
    elif conflict == "review_set":
        payload["review_ids"].pop()
    else:
        admin.post(f"/api/review/items/{payload['review_ids'][0]}/approve", json={})
    before_story = admin.get(f"/api/stories/{story['id']}").json()
    before_reviews = admin.get("/api/review/items?status=all").json()
    response = admin.post(f"/api/review/stories/{story['id']}/{decision}", json=payload)
    assert response.status_code == 409
    assert admin.get(f"/api/stories/{story['id']}").json() == before_story
    assert admin.get("/api/review/items?status=all").json() == before_reviews


@pytest.mark.parametrize("field", [THEME_FIELD, TROPE_FIELD, KEYWORD_FIELD, "Unknown"])
def test_group_cannot_edit_term_fields_or_unknown_fields(theme_review_clients, field):
    admin, contributor = theme_review_clients
    story = propose(contributor, contributor.get("/api/stories").json()["items"][0], {"Source": "Proposal"})
    payload = {**review_payload(admin, story), "fields": {field: "Forbidden"}}
    assert admin.post(f"/api/review/stories/{story['id']}/approve", json=payload).status_code == 400
    assert len(review_payload(admin, story)["review_ids"]) == 1


def test_rejection_does_not_overwrite_later_admin_change(theme_review_clients):
    admin, contributor = theme_review_clients
    story = propose(contributor, contributor.get("/api/stories").json()["items"][0], {"Source": "Contributor", "Abstract (Eng)": "Proposal"})
    story = propose(admin, story, {"Source": "Admin"})
    payload = review_payload(admin, story)
    response = admin.post(f"/api/review/stories/{story['id']}/reject", json=payload)
    assert response.status_code == 409
    after = admin.get(f"/api/stories/{story['id']}").json()
    assert after["fields"]["Source"] == "Admin"
    assert after["fields"]["Abstract (Eng)"] == "Proposal"
    assert len(review_payload(admin, after)["review_ids"]) == 2


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_group_decision_rolls_back_on_failure(theme_review_clients, monkeypatch, decision):
    from app.services import reviews

    admin, contributor = theme_review_clients
    story = propose(contributor, contributor.get("/api/stories").json()["items"][0], {"Source": "Contributor", "Abstract (Eng)": "Proposal"})
    payload = review_payload(admin, story)
    if decision == "approve":
        payload["fields"] = {"Source": "Admin"}
    before = admin.get(f"/api/stories/{story['id']}").json()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Simulated review failure")

    monkeypatch.setattr(reviews, "record_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="Simulated review failure"):
        admin.post(f"/api/review/stories/{story['id']}/{decision}", json=payload)
    assert admin.get(f"/api/stories/{story['id']}").json() == before
    assert review_payload(admin, story) == {key: payload[key] for key in ("expected_story_version", "review_ids")}


def test_complete_story_review_is_not_limited_by_queue_page(theme_review_clients):
    from app.db.models import ReviewItem, ReviewStatus, ReviewType

    admin, contributor = theme_review_clients
    story = propose(contributor, contributor.get("/api/stories").json()["items"][0], {"Source": "Proposal"})
    with admin.app.state.session_factory() as session:
        session.add_all([ReviewItem(
            dataset_id=story["dataset_id"], subject_table="stories", subject_id=story["id"],
            review_type=ReviewType.STORY_UPDATED, status=ReviewStatus.PENDING,
            metadata_json={"change_kind": "story_field", "field_name": "Source", "previous_value": "Proposal",
                           "current_value": "Proposal", "story_version": story["version"]},
        ) for _ in range(205)])
        session.commit()
    assert len(admin.get("/api/review/items?limit=200").json()) == 200
    payload = review_payload(admin, story)
    assert len(payload["review_ids"]) == 206
    response = admin.post(f"/api/review/stories/{story['id']}/approve", json=payload)
    assert response.status_code == 200
    assert len(response.json()) == 206


def test_group_approval_validates_admin_edits_atomically(theme_review_clients):
    admin, contributor = theme_review_clients
    story = propose(contributor, contributor.get("/api/stories").json()["items"][0], {"Source": "Proposal"})
    payload = {**review_payload(admin, story), "fields": {"Source": "Admin", "date of recording": "invalid"}}
    assert admin.post(f"/api/review/stories/{story['id']}/approve", json=payload).status_code == 400
    assert admin.get(f"/api/stories/{story['id']}").json()["fields"]["Source"] == "Proposal"
    assert len(review_payload(admin, story)["review_ids"]) == 1


def test_group_cannot_resolve_term_reviews(theme_review_clients):
    admin, contributor = theme_review_clients
    story = propose(contributor, contributor.get("/api/stories").json()["items"][0], {"Source": "Proposal", THEME_FIELD: "New theme"})
    payload = review_payload(admin, story)
    theme = next(item for item in admin.get("/api/review/items").json() if item["metadata"].get("field_name") == THEME_FIELD)
    payload["review_ids"].append(theme["id"])
    assert admin.post(f"/api/review/stories/{story['id']}/reject", json=payload).status_code == 409
    assert admin.get(f"/api/review/items/{theme['id']}").json()["status"] == "pending"


def test_group_version_check_reads_database_despite_cached_story(theme_review_clients):
    from app.db.models import Story
    from app.services.reviews import ReviewConflictError, resolve_story_data_review

    admin, contributor = theme_review_clients
    story = propose(contributor, contributor.get("/api/stories").json()["items"][0], {"Source": "Proposal"})
    payload = review_payload(admin, story)
    with admin.app.state.session_factory() as stale_session:
        cached_story = stale_session.get(Story, story["id"])
        assert cached_story.version == story["version"]
        approved = admin.post(f"/api/review/stories/{story['id']}/approve", json=payload)
        assert approved.status_code == 200
        with pytest.raises(ReviewConflictError, match="Story changed"):
            resolve_story_data_review(stale_session, story_id=story["id"], approve=False,
                                      actor_user_id=approved.json()[0]["resolved_by_user_id"], **payload)
        stale_session.rollback()
    assert admin.get(f"/api/stories/{story['id']}").json()["fields"]["Source"] == "Proposal"
