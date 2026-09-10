from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, UserRole, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def _build_app(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'contributor-term-territories.db'}")
    session_factory = build_session_factory(engine)
    return create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _row(
    *,
    title: str,
    territory: str,
    tropes: str = "§§ Shared Trope",
    themes: str = "§§ Shared Theme",
    keywords: str = "shared keyword",
) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row["territory"] = territory
    row[TROPE_FIELD] = tropes
    row[THEME_FIELD] = themes
    row[KEYWORD_FIELD] = keywords
    return row


def _upload_active_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", _csv_bytes(rows), "text/csv")},
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


def _term_by_text(items: list[dict], text: str) -> dict:
    return next(item for item in items if item["text"] == text)


def _assert_detail_scope(response, story_ids: list[str]) -> None:
    assert response.status_code == 200
    body = response.json()
    assert body["story_count"] == len(story_ids)
    assert [story["id"] for story in body["stories"]] == story_ids


def test_contributor_term_story_reads_are_limited_to_assigned_territories(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = _build_app(tmp_path)

    with TestClient(app) as admin_client, TestClient(app) as contributor_client, TestClient(app) as guest_client:
        authenticate_admin(admin_client)
        _upload_active_dataset(
            admin_client,
            [
                _row(title="Tahiti Story", territory="Tahiti"),
                _row(title="Moorea Story", territory="Moorea"),
            ],
        )
        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Scoped Contributor",
            role=UserRole.CONTRIBUTOR,
            password="contributor-password",
            territories=["Tahiti"],
        )
        authenticate_role(
            admin_client,
            guest_client,
            email="guest@example.com",
            display_name="Guest Reader",
            role=UserRole.GUEST,
            password="guest-password",
        )

        story_ids_by_title = {
            story["title"]: story["id"]
            for story in admin_client.get("/api/stories").json()["items"]
        }
        tahiti_story_id = story_ids_by_title["Tahiti Story"]
        all_story_ids = [tahiti_story_id, story_ids_by_title["Moorea Story"]]

        admin_trope = _term_by_text(
            admin_client.get("/api/tropes?include_story_ids=true").json(), "Shared Trope"
        )
        admin_theme = _term_by_text(
            admin_client.get("/api/themes?include_story_ids=true").json(), "Shared Theme"
        )
        admin_keyword = _term_by_text(
            admin_client.get("/api/keywords").json(), "shared keyword"
        )

        contributor_trope = _term_by_text(
            contributor_client.get("/api/tropes?include_story_ids=true").json(), "Shared Trope"
        )
        contributor_theme = _term_by_text(
            contributor_client.get("/api/themes?include_story_ids=true").json(), "Shared Theme"
        )
        contributor_keyword = _term_by_text(
            contributor_client.get("/api/keywords").json(), "shared keyword"
        )
        contributor_duplicate_trope = contributor_client.post(
            "/api/tropes", json={"text": "Shared Trope"}
        )
        contributor_duplicate_keyword = contributor_client.post(
            "/api/keywords", json={"text": "shared keyword"}
        )

        guest_trope = _term_by_text(
            guest_client.get("/api/tropes?include_story_ids=true").json(), "Shared Trope"
        )
        guest_theme = _term_by_text(
            guest_client.get("/api/themes?include_story_ids=true").json(), "Shared Theme"
        )
        guest_keyword = _term_by_text(
            guest_client.get("/api/keywords").json(), "shared keyword"
        )

        contributor_trope_detail = contributor_client.get(f"/api/tropes/{admin_trope['id']}")
        contributor_theme_detail = contributor_client.get(f"/api/themes/{admin_theme['id']}")
        contributor_keyword_detail = contributor_client.get(f"/api/keywords/{admin_keyword['id']}")
        guest_trope_detail = guest_client.get(f"/api/tropes/{admin_trope['id']}")
        guest_theme_detail = guest_client.get(f"/api/themes/{admin_theme['id']}")
        guest_keyword_detail = guest_client.get(f"/api/keywords/{admin_keyword['id']}")
        admin_trope_detail = admin_client.get(f"/api/tropes/{admin_trope['id']}")
        admin_theme_detail = admin_client.get(f"/api/themes/{admin_theme['id']}")
        admin_keyword_detail = admin_client.get(f"/api/keywords/{admin_keyword['id']}")

    assert admin_trope["story_count"] == 2
    assert admin_trope["story_ids"] == all_story_ids
    assert admin_theme["story_count"] == 2
    assert admin_theme["story_ids"] == all_story_ids
    assert admin_keyword["story_count"] == 2

    assert contributor_trope["story_count"] == 1
    assert contributor_trope["story_ids"] == [tahiti_story_id]
    assert contributor_theme["story_count"] == 1
    assert contributor_theme["story_ids"] == [tahiti_story_id]
    assert contributor_keyword["story_count"] == 1
    assert contributor_duplicate_trope.status_code == 200
    assert contributor_duplicate_trope.json()["created"] is False
    assert contributor_duplicate_trope.json()["trope"]["story_count"] == 1
    assert contributor_duplicate_keyword.status_code == 200
    assert contributor_duplicate_keyword.json()["created"] is False
    assert contributor_duplicate_keyword.json()["keyword"]["story_count"] == 1

    assert guest_trope["story_count"] == 2
    assert guest_trope["story_ids"] == all_story_ids
    assert guest_theme["story_count"] == 2
    assert guest_theme["story_ids"] == all_story_ids
    assert guest_keyword["story_count"] == 2

    _assert_detail_scope(contributor_trope_detail, [tahiti_story_id])
    _assert_detail_scope(contributor_theme_detail, [tahiti_story_id])
    _assert_detail_scope(contributor_keyword_detail, [tahiti_story_id])
    _assert_detail_scope(guest_trope_detail, all_story_ids)
    _assert_detail_scope(guest_theme_detail, all_story_ids)
    _assert_detail_scope(guest_keyword_detail, all_story_ids)
    _assert_detail_scope(admin_trope_detail, all_story_ids)
    _assert_detail_scope(admin_theme_detail, all_story_ids)
    _assert_detail_scope(admin_keyword_detail, all_story_ids)


def test_contributor_unused_term_lists_use_assigned_territory_usage(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    app = _build_app(tmp_path)

    with TestClient(app) as admin_client, TestClient(app) as contributor_client, TestClient(app) as guest_client:
        authenticate_admin(admin_client)
        _upload_active_dataset(
            admin_client,
            [
                _row(
                    title="Tahiti Story",
                    territory="Tahiti",
                    tropes="§§ A visible trope",
                    themes="§§ A visible theme",
                    keywords="a visible keyword",
                ),
                _row(
                    title="Moorea Story",
                    territory="Moorea",
                    tropes="§§ B outside trope",
                    themes="§§ B outside theme",
                    keywords="b outside keyword",
                ),
            ],
        )
        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Scoped Contributor",
            role=UserRole.CONTRIBUTOR,
            password="contributor-password",
            territories=["Tahiti"],
        )
        authenticate_role(
            admin_client,
            guest_client,
            email="guest@example.com",
            display_name="Guest Reader",
            role=UserRole.GUEST,
            password="guest-password",
        )

        contributor_tropes = contributor_client.get(
            "/api/tropes", params={"unused_only": True, "limit": 1}
        )
        contributor_themes = contributor_client.get(
            "/api/themes", params={"unused_only": True, "limit": 1}
        )
        contributor_keywords = contributor_client.get(
            "/api/keywords", params={"unused_only": True, "limit": 1}
        )

        admin_tropes = admin_client.get("/api/tropes", params={"unused_only": True})
        admin_themes = admin_client.get("/api/themes", params={"unused_only": True})
        admin_keywords = admin_client.get("/api/keywords", params={"unused_only": True})
        guest_tropes = guest_client.get("/api/tropes", params={"unused_only": True})
        guest_themes = guest_client.get("/api/themes", params={"unused_only": True})
        guest_keywords = guest_client.get("/api/keywords", params={"unused_only": True})

    assert contributor_tropes.status_code == 200
    assert [(item["text"], item["story_count"]) for item in contributor_tropes.json()] == [
        ("B outside trope", 0)
    ]
    assert contributor_tropes.json()[0]["story_ids"] == []
    assert contributor_themes.status_code == 200
    assert [(item["text"], item["story_count"]) for item in contributor_themes.json()] == [
        ("B outside theme", 0)
    ]
    assert contributor_themes.json()[0]["story_ids"] == []
    assert contributor_keywords.status_code == 200
    assert [(item["text"], item["story_count"]) for item in contributor_keywords.json()] == [
        ("b outside keyword", 0)
    ]

    assert admin_tropes.status_code == 200
    assert admin_themes.status_code == 200
    assert admin_keywords.status_code == 200
    assert guest_tropes.status_code == 200
    assert guest_themes.status_code == 200
    assert guest_keywords.status_code == 200
    assert admin_tropes.json() == []
    assert admin_themes.json() == []
    assert admin_keywords.json() == []
    assert guest_tropes.json() == []
    assert guest_themes.json() == []
    assert guest_keywords.json() == []
