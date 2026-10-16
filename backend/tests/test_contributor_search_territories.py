from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from app.db import UserRole, build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated.*"
)


def _build_app(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'contributor-search-territories.db'}")
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


def _row(*, title: str, territory: str, local_prefix: str) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row["territory"] = territory
    row[TROPE_FIELD] = f"§§ Shared scope trope\n§§ {local_prefix} scope trope"
    row[THEME_FIELD] = f"§§ Shared scope theme\n§§ {local_prefix} scope theme"
    row[KEYWORD_FIELD] = f"shared scope keyword ; {local_prefix.lower()} scope keyword"
    return row


def _search(client: TestClient, path: str, query: str) -> dict:
    response = client.post(
        path,
        json={"query": query, "limit": 10, "include_string_matches": True},
    )
    assert response.status_code == 200
    return response.json()


def _item_by_text(payload: dict, text: str) -> dict:
    return next(item for item in payload["string_match_items"] if item["text"].lower() == text.lower())


@pytest.mark.parametrize("territories", [[], ["Tahiti"]])
def test_contributor_search_includes_all_territories(monkeypatch, tmp_path, territories) -> None:
    configure_auth_env(monkeypatch)
    app = _build_app(tmp_path)

    with TestClient(app) as admin_client, TestClient(app) as contributor_client, TestClient(app) as guest_client:
        authenticate_admin(admin_client)
        upload_response = admin_client.post(
            "/api/dataset/upload",
            files={
                "file": (
                    "search-territories.csv",
                    _csv_bytes(
                        [
                            _row(title="Tahiti Story", territory="Tahiti", local_prefix="Tahiti"),
                            _row(title="Moorea Story", territory="Moorea", local_prefix="Moorea"),
                        ]
                    ),
                    "text/csv",
                )
            },
        )
        assert upload_response.status_code == 201
        rebuild_response = admin_client.post("/api/dataset/rebuild")
        assert rebuild_response.status_code == 200
        assert admin_client.app.state.job_runner.process_next_job() is True

        authenticate_role(
            admin_client,
            contributor_client,
            email="contributor@example.com",
            display_name="Scoped Contributor",
            role=UserRole.CONTRIBUTOR,
            password="contributor-password",
            territories=territories,
        )
        authenticate_role(
            admin_client,
            guest_client,
            email="guest@example.com",
            display_name="Guest Reader",
            role=UserRole.GUEST,
            password="guest-password",
        )

        search_cases = [
            ("/api/search/tropes", "scope trope", "Shared scope trope", "Tahiti scope trope", "Moorea scope trope"),
            ("/api/search/themes", "scope theme", "Shared scope theme", "Tahiti scope theme", "Moorea scope theme"),
            (
                "/api/search/keywords",
                "scope keyword",
                "shared scope keyword",
                "tahiti scope keyword",
                "moorea scope keyword",
            ),
        ]
        for path, query, shared_text, visible_text, outside_text in search_cases:
            contributor_search = _search(contributor_client, path, query)
            admin_search = _search(admin_client, path, query)
            guest_search = _search(guest_client, path, query)

            assert contributor_search == guest_search == admin_search
            assert _item_by_text(contributor_search, shared_text)["story_count"] == 2
            assert _item_by_text(contributor_search, visible_text)["story_count"] == 1
            assert _item_by_text(contributor_search, outside_text)["story_count"] == 1

            assert _item_by_text(admin_search, shared_text)["story_count"] == 2
            assert _item_by_text(admin_search, outside_text)["story_count"] == 1
            assert _item_by_text(guest_search, shared_text)["story_count"] == 2
            assert _item_by_text(guest_search, outside_text)["story_count"] == 1
