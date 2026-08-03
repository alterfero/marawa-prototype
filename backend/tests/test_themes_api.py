import csv
import io

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, THEME_FIELD
from app.db import Dataset, DatasetStatus, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(*, title: str, themes: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[THEME_FIELD] = themes
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
        staged_dataset.status = DatasetStatus.ACTIVE
        session.commit()


def test_theme_management_lists_details_and_updates_status(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'themes-api.db'}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", themes="§§ Creation"),
                make_row(title="Story Two", themes="§§ Ocean\n§§ Creation"),
            ],
        )

        themes = client.get("/api/themes").json()
        creation = next(theme for theme in themes if theme["text"] == "Creation")
        detail = client.get(f"/api/themes/{creation['id']}")
        confirmation = client.put(
            f"/api/themes/{creation['id']}/confirmation",
            json={
                "expected_theme_version": creation["version"],
                "confirmation_status": "canonical",
            },
        )
        stale_confirmation = client.put(
            f"/api/themes/{creation['id']}/confirmation",
            json={
                "expected_theme_version": creation["version"],
                "confirmation_status": "unconfirmed",
            },
        )

    assert creation["confirmation_status"] == "unconfirmed"
    assert creation["story_count"] == 2
    assert detail.status_code == 200
    assert [story["title"] for story in detail.json()["stories"]] == ["Story One", "Story Two"]
    assert confirmation.status_code == 200
    assert confirmation.json()["theme"]["confirmation_status"] == "canonical"
    assert confirmation.json()["theme"]["version"] == 2
    assert stale_confirmation.status_code == 409
    assert stale_confirmation.json()["code"] == "theme_version_conflict"


def test_theme_management_rename_and_delete_update_story_theme_field(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'themes-mutation-api.db'}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", themes="§§ Creation")])
        theme = client.get("/api/themes").json()[0]
        story = client.get("/api/stories").json()["items"][0]

        renamed = client.put(
            f"/api/themes/{theme['id']}",
            json={
                "expected_theme_version": theme["version"],
                "text": "Origin",
            },
        )
        story_after_rename = client.get(f"/api/stories/{story['id']}")
        deleted = client.delete(
            f"/api/themes/{theme['id']}?expected_theme_version=2&remove_from_all_stories=true"
        )
        story_after_delete = client.get(f"/api/stories/{story['id']}")

    assert renamed.status_code == 200
    assert renamed.json()["theme"]["text"] == "Origin"
    assert story_after_rename.json()["fields"][THEME_FIELD] == "§§ Origin"
    assert deleted.status_code == 200
    assert deleted.json()["affected_story_count"] == 1
    assert story_after_delete.json()["fields"][THEME_FIELD] == ""


def test_story_theme_field_updates_create_managed_theme_assignments(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'story-theme-update-api.db'}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One")])
        story = client.get("/api/stories").json()["items"][0]

        updated = client.patch(
            f"/api/stories/{story['id']}",
            json={
                "expected_story_version": story["version"],
                "fields": {THEME_FIELD: "Creation; Ocean"},
            },
        )
        themes = client.get("/api/themes").json()

    assert updated.status_code == 200
    assert updated.json()["story"]["fields"][THEME_FIELD] == "§§ Creation\n§§ Ocean"
    assert [(theme["text"], theme["story_count"]) for theme in themes] == [("Creation", 1), ("Ocean", 1)]


def test_unconfirmed_theme_merges_into_canonical_theme(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'theme-merge-api.db'}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Origin only", themes="§§ Origin"),
                make_row(title="Both themes", themes="§§ Creation\n§§ Origin"),
            ],
        )
        themes = client.get("/api/themes").json()
        creation = next(theme for theme in themes if theme["text"] == "Creation")
        origin = next(theme for theme in themes if theme["text"] == "Origin")
        canonicalized = client.put(
            f"/api/themes/{creation['id']}/confirmation",
            json={
                "expected_theme_version": creation["version"],
                "confirmation_status": "canonical",
            },
        )
        merged = client.post(
            f"/api/themes/{origin['id']}/merge",
            json={
                "expected_source_theme_version": origin["version"],
                "target_theme_id": creation["id"],
            },
        )
        themes_after_merge = client.get("/api/themes").json()
        stories_after_merge = client.get("/api/stories").json()["items"]

    assert canonicalized.status_code == 200
    assert merged.status_code == 200
    assert merged.json()["source_theme_id"] == origin["id"]
    assert merged.json()["target_theme"]["id"] == creation["id"]
    assert merged.json()["affected_story_count"] == 2
    assert [(theme["text"], theme["confirmation_status"], theme["story_count"]) for theme in themes_after_merge] == [
        ("Creation", "canonical", 2)
    ]
    assert {story["fields"][THEME_FIELD] for story in stories_after_merge} == {"§§ Creation"}


def test_unconfirmed_theme_merges_into_unconfirmed_theme(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'theme-unconfirmed-merge-api.db'}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Origin only", themes="§§ Origin"),
                make_row(title="Both themes", themes="§§ Creation\n§§ Origin"),
            ],
        )
        themes = client.get("/api/themes").json()
        target = next(theme for theme in themes if theme["text"] == "Creation")
        source = next(theme for theme in themes if theme["text"] == "Origin")
        merged = client.post(
            f"/api/themes/{source['id']}/merge",
            json={
                "expected_source_theme_version": source["version"],
                "target_theme_id": target["id"],
            },
        )
        themes_after_merge = client.get("/api/themes").json()

    assert merged.status_code == 200
    assert merged.json()["target_theme"]["id"] == target["id"]
    assert [(theme["text"], theme["confirmation_status"], theme["story_count"]) for theme in themes_after_merge] == [
        ("Creation", "unconfirmed", 2)
    ]


def test_story_theme_assignments_are_independent_and_removable(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'story-theme-assignment-api.db'}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One")])
        story = client.get("/api/stories").json()["items"][0]

        added = client.post(
            f"/api/stories/{story['id']}/themes",
            json={
                "expected_story_version": story["version"],
                "text": "Creation",
            },
        )
        detail_after_add = client.get(f"/api/stories/{story['id']}")
        themes_after_add = client.get("/api/themes")
        removed = client.request(
            "DELETE",
            f"/api/stories/{story['id']}/themes/{added.json()['theme']['id']}",
            json={"expected_story_version": added.json()["story_version"]},
        )
        detail_after_remove = client.get(f"/api/stories/{story['id']}")
        themes_after_remove = client.get("/api/themes")

    assert added.status_code == 201
    assert added.json()["theme"]["confirmation_status"] == "unconfirmed"
    assert detail_after_add.status_code == 200
    assert detail_after_add.json()["fields"][THEME_FIELD] == "§§ Creation"
    assert [theme["text"] for theme in detail_after_add.json()["themes"]] == ["Creation"]
    assert detail_after_add.json()["tropes"] == []
    assert [(theme["text"], theme["story_count"]) for theme in themes_after_add.json()] == [("Creation", 1)]
    assert removed.status_code == 200
    assert detail_after_remove.json()["fields"][THEME_FIELD] == ""
    assert detail_after_remove.json()["themes"] == []
    assert [(theme["text"], theme["story_count"]) for theme in themes_after_remove.json()] == [("Creation", 0)]
