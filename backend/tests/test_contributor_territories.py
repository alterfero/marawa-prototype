from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env, redeem_token
from tests.search_fakes import FakeEmbeddingBackend


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def build_app(tmp_path, name: str):
    db_path = tmp_path / name
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    return create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(*, title: str, territory: str) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row["territory"] = territory
    return row


def upload_active_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
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


def create_contributor(
    admin_client: TestClient,
    *,
    email: str,
    territories: list[str],
) -> dict:
    response = admin_client.post(
        "/api/admin/users",
        json={
            "email": email,
            "display_name": "Territory Contributor",
            "role": "contributor",
            "territories": territories,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_admin_manages_contributor_territory_collections(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "contributor-territory-admin.db")

    with TestClient(app) as admin_client:
        authenticate_admin(admin_client)
        upload_active_dataset(
            admin_client,
            [
                make_row(title="Tahiti Story", territory="Tahiti"),
                make_row(title="Moorea Story", territory="Moorea"),
            ],
        )

        territory_response = admin_client.get("/api/admin/users/territories")
        assert territory_response.status_code == 200
        assert set(territory_response.json()["items"]) == {"Tahiti", "Moorea"}

        default_response = admin_client.post(
            "/api/admin/users",
            json={
                "email": "unassigned@example.com",
                "display_name": "Unassigned Contributor",
                "role": "contributor",
            },
        )
        assert default_response.status_code == 201
        assert default_response.json()["user"]["territories"] == []

        created = create_contributor(
            admin_client,
            email="scoped@example.com",
            territories=["Tahiti", "Moorea"],
        )
        user_id = created["user"]["id"]
        assert created["user"]["territories"] == ["Tahiti", "Moorea"]
        assert created["user"]["version"] == 1

        users_response = admin_client.get("/api/admin/users")
        assert users_response.status_code == 200
        users_by_email = {item["email"]: item for item in users_response.json()}
        assert users_by_email["admin@example.com"]["territories"] == []
        assert users_by_email["unassigned@example.com"]["territories"] == []
        assert users_by_email["scoped@example.com"]["territories"] == ["Tahiti", "Moorea"]

        update_response = admin_client.patch(
            f"/api/admin/users/{user_id}",
            json={
                "expected_user_version": created["user"]["version"],
                "display_name": "Scoped Contributor",
                "role": "contributor",
                "territories": ["Moorea"],
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["territories"] == ["Moorea"]
        assert update_response.json()["version"] == 2

        updated_users = admin_client.get("/api/admin/users").json()
        updated_user = next(item for item in updated_users if item["id"] == user_id)
        assert updated_user["territories"] == ["Moorea"]
        assert updated_user["version"] == 2


def test_admin_user_territory_updates_require_a_current_user_version(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "contributor-territory-user-version.db")

    with TestClient(app) as admin_client:
        authenticate_admin(admin_client)
        created = create_contributor(
            admin_client,
            email="versioned@example.com",
            territories=["Tahiti"],
        )
        user = created["user"]

        missing_version = admin_client.patch(
            f"/api/admin/users/{user['id']}",
            json={"territories": ["Moorea"]},
        )
        assert missing_version.status_code == 422
        assert missing_version.json()["code"] == "request_validation_error"

        update_response = admin_client.patch(
            f"/api/admin/users/{user['id']}",
            json={
                "expected_user_version": user["version"],
                "territories": ["Moorea"],
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["territories"] == ["Moorea"]
        assert update_response.json()["version"] == user["version"] + 1

        stale_response = admin_client.patch(
            f"/api/admin/users/{user['id']}",
            json={
                "expected_user_version": user["version"],
                "territories": ["Tahiti"],
            },
        )
        assert stale_response.status_code == 409
        assert stale_response.json()["code"] == "user_version_conflict"
        assert stale_response.json()["details"] == {"current_user_version": user["version"] + 1}

        current_user = next(
            item for item in admin_client.get("/api/admin/users").json() if item["id"] == user["id"]
        )
        assert current_user["territories"] == ["Moorea"]
        assert current_user["version"] == user["version"] + 1


def test_contributor_reads_all_stories_but_only_edits_assigned_territories(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "contributor-territory-stories.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_active_dataset(
            admin_client,
            [
                make_row(title="Tahiti Story", territory="Tahiti"),
                make_row(title="Moorea Story", territory="Moorea"),
            ],
        )
        invitation = create_contributor(
            admin_client,
            email="contributor@example.com",
            territories=["Tahiti"],
        )
        redeem_token(
            contributor_client,
            token=invitation["invite_token"],
            new_password="contributor-password",
        )

        list_response = contributor_client.get("/api/stories")
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 2
        assert [story["title"] for story in list_response.json()["items"]] == ["Tahiti Story", "Moorea Story"]
        tahiti_story = list_response.json()["items"][0]

        admin_stories = admin_client.get("/api/stories").json()["items"]
        moorea_story = next(story for story in admin_stories if story["territory"] == "Moorea")

        allowed_detail = contributor_client.get(f"/api/stories/{tahiti_story['id']}")
        assert allowed_detail.status_code == 200
        assert allowed_detail.json()["fields"]["territory"] == "Tahiti"
        assert allowed_detail.json()["can_edit"] is True

        readonly_detail = contributor_client.get(f"/api/stories/{moorea_story['id']}")
        assert readonly_detail.status_code == 200
        assert readonly_detail.json()["can_edit"] is False

        for kind in ("tropes", "keywords", "themes"):
            assert contributor_client.get(f"/api/stories/{moorea_story['id']}/{kind}").status_code == 200

        denied_create = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "Blocked Story", "territory": "Moorea"},
            },
        )
        assert denied_create.status_code == 403
        assert denied_create.json()["code"] == "story_territory_forbidden"

        allowed_create = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "New Tahiti Story", "territory": "Tahiti"},
            },
        )
        assert allowed_create.status_code == 201
        assert allowed_create.json()["story"]["fields"]["territory"] == "Tahiti"

        denied_update = contributor_client.patch(
            f"/api/stories/{moorea_story['id']}",
            json={
                "expected_story_version": moorea_story["version"],
                "fields": {"Story title (Eng)": "Blocked Update"},
            },
        )
        assert denied_update.status_code == 403
        assert denied_update.json()["code"] == "story_territory_forbidden"

        allowed_update = contributor_client.patch(
            f"/api/stories/{tahiti_story['id']}",
            json={
                "expected_story_version": tahiti_story["version"],
                "fields": {"1-sentence summary": "An allowed update."},
            },
        )
        assert allowed_update.status_code == 200
        updated_tahiti_story = allowed_update.json()["story"]

        denied_territory_move = contributor_client.patch(
            f"/api/stories/{tahiti_story['id']}",
            json={
                "expected_story_version": updated_tahiti_story["version"],
                "fields": {"territory": "Moorea"},
            },
        )
        assert denied_territory_move.status_code == 403
        assert denied_territory_move.json()["code"] == "story_territory_forbidden"

        visible_after_mutations = contributor_client.get("/api/stories")
        assert visible_after_mutations.status_code == 200
        assert [story["territory"] for story in visible_after_mutations.json()["items"]] == ["Tahiti", "Moorea", "Tahiti"]


def test_only_admins_can_create_a_new_territory(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "contributor-new-territory.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_active_dataset(
            admin_client,
            [make_row(title="Tahiti Story", territory="Tahiti")],
        )
        invitation = create_contributor(
            admin_client,
            email="new-territory-contributor@example.com",
            territories=["Tahiti", "New Atoll", "Future Island"],
        )
        redeem_token(
            contributor_client,
            token=invitation["invite_token"],
            new_password="contributor-password",
        )

        assert admin_client.get("/api/stories/territories").json() == {"items": ["Tahiti"]}
        assert contributor_client.get("/api/stories/territories").json() == {"items": ["Tahiti"]}

        denied_create = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "Blocked New Atoll Story", "territory": "New Atoll"},
            },
        )
        assert denied_create.status_code == 403
        assert denied_create.json()["code"] == "story_territory_forbidden"
        assert admin_client.get("/api/stories").json()["total"] == 1

        tahiti_story = admin_client.get("/api/stories").json()["items"][0]
        denied_territory_update = contributor_client.patch(
            f"/api/stories/{tahiti_story['id']}",
            json={
                "expected_story_version": tahiti_story["version"],
                "fields": {"territory": "Future Island"},
            },
        )
        assert denied_territory_update.status_code == 403
        assert denied_territory_update.json()["code"] == "story_territory_forbidden"

        admin_create = admin_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "New Atoll Story", "territory": "New Atoll"},
            },
        )
        assert admin_create.status_code == 201
        assert admin_create.json()["story"]["fields"]["territory"] == "New Atoll"
        assert admin_create.json()["dataset_version"] == 2

        assert admin_client.get("/api/stories/territories").json() == {"items": ["New Atoll", "Tahiti"]}
        assert contributor_client.get("/api/stories/territories").json() == {"items": ["New Atoll", "Tahiti"]}

        allowed_create = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 2,
                "fields": {"Story title (Eng)": "Contributor New Atoll Story", "territory": "new atoll"},
            },
        )
        assert allowed_create.status_code == 201
        assert allowed_create.json()["story"]["fields"]["territory"] == "New Atoll"


def test_unassigned_contributor_can_read_but_cannot_create_or_edit_stories(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "unassigned-contributor-territory.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_active_dataset(
            admin_client,
            [make_row(title="Tahiti Story", territory="Tahiti")],
        )
        invitation = admin_client.post(
            "/api/admin/users",
            json={
                "email": "unassigned-contributor@example.com",
                "display_name": "Unassigned Contributor",
                "role": "contributor",
            },
        )
        assert invitation.status_code == 201
        assert invitation.json()["user"]["territories"] == []
        redeem_token(
            contributor_client,
            token=invitation.json()["invite_token"],
            new_password="contributor-password",
        )

        story = admin_client.get("/api/stories").json()["items"][0]
        assert contributor_client.get("/api/stories").json()["items"] == [story]

        detail_response = contributor_client.get(f"/api/stories/{story['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["can_edit"] is False
        assert contributor_client.get("/api/stories/territories").json() == {"items": []}

        edit_response = contributor_client.patch(
            f"/api/stories/{story['id']}",
            json={"expected_story_version": story["version"], "completeness": "pending review"},
        )
        assert edit_response.status_code == 403
        assert edit_response.json()["code"] == "story_territory_forbidden"

        create_response = contributor_client.post(
            "/api/stories",
            json={
                "expected_dataset_version": 1,
                "fields": {"Story title (Eng)": "Blocked Story", "territory": "Tahiti"},
            },
        )
        assert create_response.status_code == 403
        assert create_response.json()["code"] == "story_territory_forbidden"


@pytest.mark.parametrize("territories", [[], ["Tahiti"]])
@pytest.mark.parametrize("story_territory", ["Moorea", ""])
def test_readable_story_outside_assigned_territories_rejects_every_mutation(
    monkeypatch, tmp_path, territories, story_territory
) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "readonly-story.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        row = make_row(title="Read-only story", territory=story_territory)
        row[TROPE_FIELD] = "§§ Original Trope"
        row[KEYWORD_FIELD] = "original keyword"
        row[THEME_FIELD] = "§§ Original Theme"
        upload_active_dataset(admin_client, [row, make_row(title="Assigned story", territory="Tahiti")])
        invitation = create_contributor(admin_client, email="reader@example.com", territories=territories)
        redeem_token(contributor_client, token=invitation["invite_token"], new_password="contributor-password")

        stories = contributor_client.get("/api/stories").json()
        assert stories["total"] == 2
        story_id = stories["items"][0]["id"]
        story_path = f"/api/stories/{story_id}"
        original = contributor_client.get(story_path).json()
        assert original["can_edit"] is False
        assert admin_client.get(story_path).json()["can_edit"] is True
        version = {"expected_story_version": original["version"]}
        requests = [
            ("PATCH", story_path, {"fields": {"Story title (Eng)": "Blocked title"}}),
            ("PATCH", story_path, {"fields": {"territory": "Tahiti"}}),
            ("PATCH", story_path, {"completeness": "pending review"}),
        ]
        for kind, id_field in (("tropes", "trope_id"), ("keywords", "keyword_id"), ("themes", "theme_id")):
            assignment_path = f"{story_path}/{kind}/{original[kind][0]['id']}"
            assert contributor_client.get(f"{story_path}/{kind}").status_code == 200
            requests.extend([
                ("POST", f"{story_path}/{kind}", {id_field: original[kind][0]["id"]}),
                ("DELETE", assignment_path, {}),
            ])
            if kind != "themes":
                requests.append(("PUT", assignment_path, {"text": "Blocked replacement"}))
            if kind == "tropes":
                requests.append(("POST", f"{assignment_path}/validate", {}))

        for method, path, payload in requests:
            response = contributor_client.request(method, path, json={**version, **payload})
            assert response.status_code == 403, (method, path, response.json())
            assert response.json()["code"] == "story_territory_forbidden"
        assert contributor_client.get(story_path).json() == original


def test_territory_assignment_changes_update_edit_access_in_existing_session(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = build_app(tmp_path, "updated-territory-access.db")

    with TestClient(app) as admin_client, TestClient(app) as contributor_client:
        authenticate_admin(admin_client)
        upload_active_dataset(admin_client, [make_row(title="Tahiti Story", territory="Tahiti")])
        invitation = create_contributor(admin_client, email="updated@example.com", territories=[])
        auth = redeem_token(contributor_client, token=invitation["invite_token"], new_password="contributor-password")
        user = auth["user"]
        story = contributor_client.get("/api/stories").json()["items"][0]
        story_path = f"/api/stories/{story['id']}"
        assert contributor_client.get(story_path).json()["can_edit"] is False

        for territories, expected_can_edit in [([" \ufeffTAHITI "], True), ([], False)]:
            update = admin_client.patch(
                f"/api/admin/users/{user['id']}",
                json={"expected_user_version": user["version"], "territories": territories},
            )
            assert update.status_code == 200
            user = update.json()
            assert contributor_client.get(story_path).json()["can_edit"] is expected_can_edit
            response = contributor_client.patch(
                story_path,
                json={"expected_story_version": story["version"], "fields": {"1-sentence summary": "Updated"}},
            )
            assert response.status_code == (200 if expected_can_edit else 403)
            if expected_can_edit:
                story = response.json()["story"]
        assert contributor_client.get("/api/stories").json()["total"] == 1
