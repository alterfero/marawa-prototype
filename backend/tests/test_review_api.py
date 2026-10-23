import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, UserRole, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(*, title: str, tropes: str = "", keywords: str = "", territory: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    row["territory"] = territory
    return row


def upload_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201
    with client.app.state.session_factory() as session:
        staged_dataset = session.scalar(
            select(Dataset)
            .where(Dataset.status == DatasetStatus.STAGED)
            .order_by(Dataset.created_at.desc(), Dataset.id.desc())
        )
        assert staged_dataset is not None
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        if active_dataset is not None and active_dataset.id != staged_dataset.id:
            active_dataset.status = DatasetStatus.ARCHIVED
        staged_dataset.status = DatasetStatus.ACTIVE
        session.commit()


def build_app(tmp_path, name: str):
    db_path = tmp_path / name
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    return create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)


@pytest.fixture
def theme_review_clients(monkeypatch, tmp_path):
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "theme-reviews.db")
    with TestClient(app) as admin, TestClient(app) as contributor:
        authenticate_admin(admin)
        rows = [make_row(title="Original", territory="Tahiti"), make_row(title="Other story", territory="Moorea")]
        for row in rows:
            row[THEME_FIELD] = "Creation; Ocean"
        upload_dataset(admin, rows)
        authenticate_role(
            admin, contributor, email="themes@example.com", display_name="Theme Contributor",
            role=UserRole.CONTRIBUTOR, password="contributor-password", territories=["Tahiti"],
        )
        yield admin, contributor


def theme_reviews(admin):
    return [item for item in admin.get("/api/review/items").json() if item["metadata"].get("field_name") == THEME_FIELD]


@pytest.mark.parametrize("decision", ["approve", "edit", "reject"])
def test_contributor_theme_creation_can_be_reviewed(theme_review_clients, decision):
    admin, contributor = theme_review_clients
    response = contributor.post(
        "/api/stories",
        json={"expected_dataset_version": 1, "fields": {
            "Story title (Eng)": "New theme story", "territory": "Tahiti", THEME_FIELD: "New theme; new theme",
        }},
    )
    assert response.status_code == 201
    story = response.json()["story"]
    created_theme_id = story["themes"][0]["id"]
    assert story["fields"][THEME_FIELD] == "§§ New theme"
    reviews = theme_reviews(admin)
    assert len(reviews) == 1
    review = reviews[0]
    assert review["review_type"] == "story_created"
    assert review["metadata"]["previous_value"] == ""
    assert review["metadata"]["current_value"] == "§§ New theme"
    assert review["metadata"]["created_theme_ids"] == [created_theme_id]

    if decision == "edit":
        edited = admin.patch(f"/api/stories/{story['id']}", json={
            "expected_story_version": story["version"], "fields": {THEME_FIELD: "Admin theme"},
        })
        assert edited.status_code == 200
    action = "reject" if decision == "reject" else "approve"
    resolved = admin.post(f"/api/review/items/{review['id']}/{action}", json={})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == ("rejected" if decision == "reject" else "approved")
    assert theme_reviews(admin) == []
    detail = contributor.get(f"/api/stories/{story['id']}").json()
    expected = {"approve": "§§ New theme", "edit": "§§ Admin theme", "reject": ""}[decision]
    assert detail["fields"][THEME_FIELD] == expected
    assert [theme["text"] for theme in detail["themes"]] == ([] if not expected else [expected.removeprefix("§§ ")])
    assert admin.get(f"/api/themes/{created_theme_id}").status_code == (200 if decision == "approve" else 404)
    exported = admin.get("/api/dataset/export.csv")
    reader = csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")))
    assert reader.fieldnames == CSV_COLUMNS
    assert next(row for row in reader if row["Story title (Eng)"] == "New theme story")[THEME_FIELD] == expected


def test_rejecting_theme_edit_restores_assignments_counts_and_csv(theme_review_clients):
    admin, contributor = theme_review_clients
    stories = contributor.get("/api/stories").json()["items"]
    story = next(item for item in stories if item["territory"] == "Tahiti")
    other = next(item for item in stories if item["territory"] == "Moorea")
    updated = contributor.patch(f"/api/stories/{story['id']}", json={
        "expected_story_version": story["version"], "fields": {THEME_FIELD: "Origin; Ocean\norigin"},
    })
    assert updated.status_code == 200
    detail = updated.json()["story"]
    review = theme_reviews(admin)[0]
    assert review["metadata"]["previous_value"] == "§§ Creation\n§§ Ocean"
    assert review["metadata"]["current_value"] == "§§ Origin\n§§ Ocean"
    assert contributor.get(f"/api/stories/{other['id']}").json()["fields"][THEME_FIELD] == "§§ Creation\n§§ Ocean"
    repeated = contributor.patch(f"/api/stories/{story['id']}", json={
        "expected_story_version": detail["version"], "fields": {THEME_FIELD: "Origin; Ocean"},
    })
    assert repeated.status_code == 200
    assert len(theme_reviews(admin)) == 1
    rejected = admin.post(f"/api/review/items/{review['id']}/reject", json={})
    assert rejected.status_code == 200
    restored = contributor.get(f"/api/stories/{story['id']}").json()
    assert restored["fields"][THEME_FIELD] == "§§ Creation\n§§ Ocean"
    assert [theme["text"] for theme in restored["themes"]] == ["Creation", "Ocean"]
    assert restored["version"] > repeated.json()["story"]["version"]
    assert [(theme["text"], theme["story_count"]) for theme in admin.get("/api/themes").json()] == [("Creation", 2), ("Ocean", 2)]


def test_theme_review_rejection_preserves_later_edits_and_shared_themes(theme_review_clients):
    admin, contributor = theme_review_clients
    story = contributor.get("/api/stories").json()["items"][0]
    first = contributor.patch(f"/api/stories/{story['id']}", json={
        "expected_story_version": story["version"], "fields": {THEME_FIELD: "Shared new theme"},
    })
    assert first.status_code == 200
    first_review = theme_reviews(admin)[0]
    theme_id = first.json()["story"]["themes"][0]["id"]
    second = contributor.patch(f"/api/stories/{story['id']}", json={
        "expected_story_version": first.json()["story"]["version"], "fields": {THEME_FIELD: "Later theme"},
    })
    assert second.status_code == 200
    assert admin.post(f"/api/review/items/{first_review['id']}/reject", json={}).status_code == 409
    assert contributor.get(f"/api/stories/{story['id']}").json()["fields"][THEME_FIELD] == "§§ Later theme"
    later_review = next(item for item in theme_reviews(admin) if item["id"] != first_review["id"])
    assert admin.post(f"/api/review/items/{later_review['id']}/reject", json={}).status_code == 200
    other = next(item for item in admin.get("/api/stories").json()["items"] if item["territory"] == "Moorea")
    assert admin.post(f"/api/stories/{other['id']}/themes", json={
        "expected_story_version": other["version"], "theme_id": theme_id,
    }).status_code == 201
    assert admin.post(f"/api/review/items/{first_review['id']}/reject", json={}).status_code == 200
    assert admin.get(f"/api/themes/{theme_id}").json()["story_count"] == 1


def test_theme_assignment_endpoints_also_create_review_work(theme_review_clients):
    admin, contributor = theme_review_clients
    story = contributor.get("/api/stories").json()["items"][0]
    added = contributor.post(f"/api/stories/{story['id']}/themes", json={
        "expected_story_version": story["version"], "text": "Assigned theme",
    })
    assert added.status_code == 201
    addition = theme_reviews(admin)[0]
    assert addition["metadata"]["current_value"] == "§§ Creation\n§§ Ocean\n§§ Assigned theme"
    theme_id = added.json()["theme"]["id"]
    deleted = contributor.request("DELETE", f"/api/stories/{story['id']}/themes/{theme_id}", json={
        "expected_story_version": added.json()["story_version"],
    })
    assert deleted.status_code == 200
    removal = next(item for item in theme_reviews(admin) if item["id"] != addition["id"])
    assert admin.post(f"/api/review/items/{removal['id']}/reject", json={}).status_code == 200
    assert admin.post(f"/api/review/items/{addition['id']}/reject", json={}).status_code == 200
    assert admin.get(f"/api/themes/{theme_id}").status_code == 404


def test_contributor_theme_changes_still_require_story_territory_access(theme_review_clients):
    admin, contributor = theme_review_clients
    other = next(item for item in admin.get("/api/stories").json()["items"] if item["territory"] == "Moorea")
    response = contributor.patch(f"/api/stories/{other['id']}", json={
        "expected_story_version": other["version"], "fields": {THEME_FIELD: "Unauthorized theme"},
    })
    assert response.status_code == 403
    assert theme_reviews(admin) == []
    assert "Unauthorized theme" not in [theme["text"] for theme in admin.get("/api/themes").json()]


def test_contributor_story_and_term_changes_create_review_items_and_admin_can_resolve(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "review-api.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_dataset(admin_client, [make_row(title="Imported Story", territory="Tahiti")])
        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Contributor User",
            role=UserRole.CONTRIBUTOR,
            password="contributor-password",
            territories=["Tahiti"],
        )

        create_response = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "Contributor Story", "territory": "Tahiti"},
                "tropes": ["New Pending Trope"],
                "keywords": ["Night Canoe"],
            },
        )
        assert create_response.status_code == 201
        story_id = create_response.json()["story"]["id"]

        review_items_response = admin_client.get("/api/review/items")
        assert review_items_response.status_code == 200
        review_items = review_items_response.json()
        assert len(review_items) == 6
        assert {item["review_type"] for item in review_items} == {
            "story_created",
            "trope_pending",
            "keyword_pending",
        }

        story_field_review = next(
            item
            for item in review_items
            if item["metadata"].get("change_kind") == "story_field"
            and item["metadata"].get("field_name") == "Story title (Eng)"
        )
        story_keyword_review = next(
            item
            for item in review_items
            if item["metadata"].get("change_kind") == "story_keyword"
            and item["metadata"].get("assignment_action") == "added"
        )
        trope_review = next(item for item in review_items if item["review_type"] == "trope_pending")
        keyword_review = next(item for item in review_items if item["review_type"] == "keyword_pending")

        approve_story_field = admin_client.post(
            f"/api/review/items/{story_field_review['id']}/approve",
            json={"note": "Story approved"},
        )
        assert approve_story_field.status_code == 200
        assert approve_story_field.json()["status"] == "approved"
        assert approve_story_field.json()["metadata"]["resolution"]["decision"] == "approved"

        territory_field_review = next(
            item
            for item in review_items
            if item["metadata"].get("change_kind") == "story_field"
            and item["metadata"].get("field_name") == "territory"
        )
        approve_territory_field = admin_client.post(
            f"/api/review/items/{territory_field_review['id']}/approve",
            json={"note": "Territory approved"},
        )
        assert approve_territory_field.status_code == 200

        reject_story_keyword = admin_client.post(
            f"/api/review/items/{story_keyword_review['id']}/reject",
            json={"note": "Keyword should not stay on this story"},
        )
        assert reject_story_keyword.status_code == 200
        assert reject_story_keyword.json()["status"] == "rejected"
        assert reject_story_keyword.json()["metadata"]["resolution"]["action"] == "reverted"

        approve_trope = admin_client.post(
            f"/api/review/items/{trope_review['id']}/approve",
            json={"note": "Trope approved"},
        )
        assert approve_trope.status_code == 200
        assert approve_trope.json()["status"] == "approved"
        assert approve_trope.json()["subject_preview"]["review_status"] == "approved"

        reject_keyword = admin_client.post(
            f"/api/review/items/{keyword_review['id']}/reject",
            json={
                "note": "Keyword rejected",
                "remove_from_all_stories": True,
            },
        )
        assert reject_keyword.status_code == 200
        rejected_keyword = reject_keyword.json()
        assert rejected_keyword["status"] == "rejected"
        assert rejected_keyword["metadata"]["resolution"]["action"] == "deleted"
        assert rejected_keyword["subject_preview"] is None

        pending_after_resolution = admin_client.get("/api/review/items").json()
        assert len(pending_after_resolution) == 1
        remaining_story_item = pending_after_resolution[0]
        assert remaining_story_item["metadata"]["change_kind"] == "story_trope"
        assert remaining_story_item["metadata"]["assignment_action"] == "added"

        approve_remaining_story_item = admin_client.post(
            f"/api/review/items/{remaining_story_item['id']}/approve",
            json={"note": "Story trope addition approved"},
        )
        assert approve_remaining_story_item.status_code == 200
        assert admin_client.get("/api/review/items").json() == []

        contributor_story = contributor_client.get(f"/api/stories/{story_id}").json()
        assert contributor_story["fields"]["Story title (Eng)"] == "Contributor Story"
        assert [item["text"] for item in contributor_story["tropes"]] == ["New Pending Trope"]
        assert contributor_story["keywords"] == []


def test_rejecting_story_field_review_reverts_only_that_field(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "review-field-revert.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_dataset(admin_client, [make_row(title="Imported Story", territory="Tahiti")])
        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Contributor User",
            role=UserRole.CONTRIBUTOR,
            password="contributor-password",
            territories=["Tahiti"],
        )

        imported_story = contributor_client.get("/api/stories").json()["items"][0]
        update_response = contributor_client.patch(
            f"/api/stories/{imported_story['id']}",
            json={
                "expected_story_version": imported_story["version"],
                "fields": {
                    "Story title (Eng)": "Edited by contributor",
                    "Abstract (Eng)": "Fresh abstract",
                },
            },
        )
        assert update_response.status_code == 200

        review_items = admin_client.get("/api/review/items").json()
        title_review = next(
            item
            for item in review_items
            if item["metadata"].get("change_kind") == "story_field"
            and item["metadata"].get("field_name") == "Story title (Eng)"
        )
        abstract_review = next(
            item
            for item in review_items
            if item["metadata"].get("change_kind") == "story_field"
            and item["metadata"].get("field_name") == "Abstract (Eng)"
        )

        reject_response = admin_client.post(
            f"/api/review/items/{title_review['id']}/reject",
            json={"note": "Keep the imported title"},
        )
        assert reject_response.status_code == 200
        assert reject_response.json()["metadata"]["resolution"]["action"] == "reverted"

        story_detail = contributor_client.get(f"/api/stories/{imported_story['id']}").json()
        assert story_detail["fields"]["Story title (Eng)"] == "Imported Story"
        assert story_detail["fields"]["Abstract (Eng)"] == "Fresh abstract"

        approve_response = admin_client.post(
            f"/api/review/items/{abstract_review['id']}/approve",
            json={"note": "Abstract approved"},
        )
        assert approve_response.status_code == 200
        assert admin_client.get("/api/review/items").json() == []


def test_admin_can_reject_pending_trope_by_merging_into_existing_trope(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "review-merge.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_dataset(admin_client, [make_row(title="Imported Story", tropes="§§ existing trope", territory="Tahiti")])
        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Contributor User",
            role=UserRole.CONTRIBUTOR,
            password="contributor-password",
            territories=["Tahiti"],
        )

        imported_story = contributor_client.get("/api/stories").json()["items"][0]
        existing_trope = contributor_client.get(f"/api/stories/{imported_story['id']}/tropes").json()["items"][0]

        add_response = contributor_client.post(
            f"/api/stories/{imported_story['id']}/tropes",
            json={
                "expected_story_version": 1,
                "text": "Pending variant trope",
            },
        )
        assert add_response.status_code == 201
        pending_trope_id = add_response.json()["trope"]["id"]

        review_items = admin_client.get("/api/review/items").json()
        trope_review = next(item for item in review_items if item["review_type"] == "trope_pending")

        reject_response = admin_client.post(
            f"/api/review/items/{trope_review['id']}/reject",
            json={
                "note": "Merge into canonical existing trope",
                "merge_target_id": existing_trope["id"],
            },
        )

        assert reject_response.status_code == 200
        body = reject_response.json()
        assert body["status"] == "rejected"
        assert body["metadata"]["resolution"]["action"] == "merged"
        assert body["metadata"]["resolution"]["merge_target_id"] == existing_trope["id"]

        story_detail = contributor_client.get(f"/api/stories/{imported_story['id']}").json()
        trope_texts = [item["text"] for item in story_detail["tropes"]]
        assert trope_texts == ["existing trope"]

        trope_detail = admin_client.get(f"/api/tropes/{pending_trope_id}")
        assert trope_detail.status_code == 404
