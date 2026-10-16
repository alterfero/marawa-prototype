from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, THEME_FIELD, TROPE_FIELD
from app.db import Dataset, DatasetStatus, UserRole, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def _build_app(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'contributor-story-count-territories.db'}")
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


def _row(*, title: str, territory: str, tropes: str, themes: str) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row["territory"] = territory
    row[TROPE_FIELD] = tropes
    row[THEME_FIELD] = themes
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


def test_story_term_counts_include_all_territories_for_contributors(monkeypatch, tmp_path) -> None:
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
                    tropes="§§ Shared Trope",
                    themes="§§ Shared Theme",
                ),
                _row(
                    title="Moorea Story",
                    territory="Moorea",
                    tropes="§§ Shared Trope\n§§ Mutation Trope\n§§ Replacement Trope",
                    themes="§§ Shared Theme\n§§ Mutation Theme",
                ),
            ],
        )
        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Tahiti Contributor",
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

        stories_by_title = {
            story["title"]: story for story in admin_client.get("/api/stories").json()["items"]
        }
        tahiti_story = stories_by_title["Tahiti Story"]
        moorea_story = stories_by_title["Moorea Story"]
        moorea_detail = admin_client.get(f"/api/stories/{moorea_story['id']}").json()
        mutation_trope = _term_by_text(moorea_detail["tropes"], "Mutation Trope")
        replacement_trope = _term_by_text(moorea_detail["tropes"], "Replacement Trope")
        mutation_theme = _term_by_text(moorea_detail["themes"], "Mutation Theme")
        shared_trope = _term_by_text(moorea_detail["tropes"], "Shared Trope")

        contributor_detail = contributor_client.get(f"/api/stories/{tahiti_story['id']}")
        contributor_tropes = contributor_client.get(f"/api/stories/{tahiti_story['id']}/tropes")
        contributor_themes = contributor_client.get(f"/api/stories/{tahiti_story['id']}/themes")
        admin_detail = admin_client.get(f"/api/stories/{tahiti_story['id']}")
        guest_detail = guest_client.get(f"/api/stories/{tahiti_story['id']}")

        assert contributor_detail.status_code == 200
        assert contributor_tropes.status_code == 200
        assert contributor_themes.status_code == 200
        assert admin_detail.status_code == 200
        assert guest_detail.status_code == 200
        assert _term_by_text(contributor_detail.json()["tropes"], "Shared Trope")["story_count"] == 2
        assert _term_by_text(contributor_detail.json()["themes"], "Shared Theme")["story_count"] == 2
        assert _term_by_text(contributor_tropes.json()["items"], "Shared Trope")["story_count"] == 2
        assert _term_by_text(contributor_themes.json()["items"], "Shared Theme")["story_count"] == 2
        assert _term_by_text(admin_detail.json()["tropes"], "Shared Trope")["story_count"] == 2
        assert _term_by_text(admin_detail.json()["themes"], "Shared Theme")["story_count"] == 2
        assert _term_by_text(guest_detail.json()["tropes"], "Shared Trope")["story_count"] == 2
        assert _term_by_text(guest_detail.json()["themes"], "Shared Theme")["story_count"] == 2

        added_trope = contributor_client.post(
            f"/api/stories/{tahiti_story['id']}/tropes",
            json={
                "expected_story_version": tahiti_story["version"],
                "trope_id": mutation_trope["id"],
            },
        )
        assert added_trope.status_code == 201
        assert added_trope.json()["trope"]["story_count"] == 2

        added_theme = contributor_client.post(
            f"/api/stories/{tahiti_story['id']}/themes",
            json={
                "expected_story_version": added_trope.json()["story_version"],
                "theme_id": mutation_theme["id"],
            },
        )
        assert added_theme.status_code == 201
        assert added_theme.json()["theme"]["story_count"] == 2

        replaced_trope = contributor_client.put(
            f"/api/stories/{tahiti_story['id']}/tropes/{shared_trope['id']}",
            json={
                "expected_story_version": added_theme.json()["story_version"],
                "trope_id": replacement_trope["id"],
            },
        )
        assert replaced_trope.status_code == 200
        assert replaced_trope.json()["trope"]["story_count"] == 2

        admin_after_mutations = admin_client.get(f"/api/stories/{tahiti_story['id']}")
        guest_after_mutations = guest_client.get(f"/api/stories/{tahiti_story['id']}")
        contributor_after_mutations = contributor_client.get(f"/api/stories/{tahiti_story['id']}")
        assert admin_after_mutations.status_code == 200
        assert guest_after_mutations.status_code == 200
        assert contributor_after_mutations.status_code == 200
        assert _term_by_text(admin_after_mutations.json()["tropes"], "Mutation Trope")["story_count"] == 2
        assert _term_by_text(admin_after_mutations.json()["tropes"], "Replacement Trope")["story_count"] == 2
        assert _term_by_text(admin_after_mutations.json()["themes"], "Mutation Theme")["story_count"] == 2
        assert _term_by_text(guest_after_mutations.json()["tropes"], "Mutation Trope")["story_count"] == 2
        assert _term_by_text(guest_after_mutations.json()["tropes"], "Replacement Trope")["story_count"] == 2
        assert _term_by_text(guest_after_mutations.json()["themes"], "Mutation Theme")["story_count"] == 2
        assert _term_by_text(contributor_after_mutations.json()["tropes"], "Mutation Trope")["story_count"] == 2
        assert _term_by_text(contributor_after_mutations.json()["tropes"], "Replacement Trope")["story_count"] == 2
        assert _term_by_text(contributor_after_mutations.json()["themes"], "Mutation Theme")["story_count"] == 2
