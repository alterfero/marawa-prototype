import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import build_engine, build_session_factory
from app.main import create_app


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


def make_row(*, title: str, tropes: str = "", keywords: str = "", territory: str = "", summary: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    row["territory"] = territory
    row["1-sentence summary"] = summary
    return row


def upload_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "stories-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as test_client:
        yield test_client


def test_stories_api_lists_story_detail_and_tropes(client: TestClient) -> None:
    upload_dataset(
        client,
        [
            make_row(
                title="Story One",
                tropes="§§ first trope\n§§ second trope",
                keywords="wolf ; moon",
                territory="Tahiti",
                summary="A short summary",
            ),
            make_row(title="Story Two", keywords="river"),
        ],
    )

    list_response = client.get("/api/stories")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] == 2
    assert [item["title"] for item in payload["items"]] == ["Story One", "Story Two"]
    assert payload["items"][0]["version"] == 1
    assert payload["items"][0]["trope_count"] == 2
    assert payload["items"][0]["keyword_count"] == 2

    story_id = payload["items"][0]["id"]

    detail_response = client.get(f"/api/stories/{story_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["fields"]["Story title (Eng)"] == "Story One"
    assert detail["fields"][TROPE_FIELD] == "§§ first trope\n§§ second trope"
    assert detail["fields"][KEYWORD_FIELD] == "wolf ; moon"
    assert [item["text"] for item in detail["tropes"]] == ["first trope", "second trope"]
    assert detail["tropes"][0]["story_count"] == 1
    assert detail["tropes"][0]["origin"] == "csv_import"
    assert detail["tropes"][0]["status"] == "validated"

    tropes_response = client.get(f"/api/stories/{story_id}/tropes")
    assert tropes_response.status_code == 200
    tropes_payload = tropes_response.json()
    assert tropes_payload["story_id"] == story_id
    assert tropes_payload["story_version"] == 1
    assert [item["text"] for item in tropes_payload["items"]] == ["first trope", "second trope"]
    assert tropes_payload["items"][0]["story_count"] == 1


def test_add_story_trope_creates_new_canonical_trope_and_queues_rebuild(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Story One")])
    story = client.get("/api/stories").json()["items"][0]

    response = client.post(
        f"/api/stories/{story['id']}/tropes",
        json={
            "expected_story_version": 1,
            "text": "Moon Bride",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["story_version"] == 2
    assert body["dataset_version"] == 2
    assert body["trope"]["text"] == "Moon Bride"
    assert body["trope"]["origin"] == "human_entered"
    assert body["trope"]["status"] == "validated"
    assert body["queued_job"]["status"] == "queued"
    assert body["queued_job"]["job_type"] == "full_rebuild"

    detail = client.get(f"/api/stories/{story['id']}").json()
    assert detail["version"] == 2
    assert detail["fields"][TROPE_FIELD] == "§§ Moon Bride"
    assert [item["text"] for item in detail["tropes"]] == ["Moon Bride"]

    dataset_status = client.get("/api/dataset/status").json()
    assert dataset_status["active_dataset_version"] == 2


def test_create_story_adds_manual_entry_with_metadata_keywords_and_tropes(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Imported Story", tropes="§§ existing trope", keywords="lagoon")])

    response = client.post(
        "/api/stories",
        json={
            "expected_dataset_version": 1,
            "fields": {
                "Entered by": "Researcher",
                "territory": "Moorea",
                "original language": "Reo Maohi",
                "Story title (Eng)": "Manual Story",
                "1-sentence summary": "A new story entered by hand.",
                TROPE_FIELD: "ignored",
                KEYWORD_FIELD: "ignored",
            },
            "tropes": ["Moon Bride", " moon bride ", "existing trope"],
            "keywords": ["breadfruit", "Breadfruit", "night canoe"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["dataset_version"] == 2
    assert body["queued_job"]["status"] == "queued"
    assert body["queued_job"]["job_type"] == "full_rebuild"
    assert body["story"]["source_row_number"] is None
    assert body["story"]["version"] == 1
    assert body["story"]["fields"]["Entered by"] == "Researcher"
    assert body["story"]["fields"]["territory"] == "Moorea"
    assert body["story"]["fields"]["original language"] == "Reo Maohi"
    assert body["story"]["fields"]["Story title (Eng)"] == "Manual Story"
    assert body["story"]["fields"][TROPE_FIELD] == "§§ Moon Bride\n§§ existing trope"
    assert body["story"]["fields"][KEYWORD_FIELD] == "breadfruit ; night canoe"
    assert [item["text"] for item in body["story"]["tropes"]] == ["Moon Bride", "existing trope"]
    assert [item["origin"] for item in body["story"]["tropes"]] == ["human_entered", "human_entered"]
    assert [item["status"] for item in body["story"]["tropes"]] == ["validated", "validated"]
    assert [item["text"] for item in body["story"]["keywords"]] == ["breadfruit", "night canoe"]

    stories_payload = client.get("/api/stories").json()
    assert stories_payload["total"] == 2
    assert [item["title"] for item in stories_payload["items"]] == ["Imported Story", "Manual Story"]

    dataset_status = client.get("/api/dataset/status").json()
    assert dataset_status["story_count"] == 2
    assert dataset_status["trope_count"] == 2
    assert dataset_status["keyword_count"] == 3
    assert dataset_status["active_dataset_version"] == 2


def test_create_story_returns_409_for_stale_expected_dataset_version(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Imported Story")])

    first_response = client.post(
        "/api/stories",
        json={
            "expected_dataset_version": 1,
            "fields": {"Story title (Eng)": "Manual Story"},
            "tropes": ["Moon Bride"],
            "keywords": ["breadfruit"],
        },
    )
    assert first_response.status_code == 201

    stale_response = client.post(
        "/api/stories",
        json={
            "expected_dataset_version": 1,
            "fields": {"Story title (Eng)": "Second Manual Story"},
            "tropes": ["Night Canoe"],
            "keywords": [],
        },
    )

    assert stale_response.status_code == 409
    assert stale_response.json() == {
        "code": "dataset_version_conflict",
        "message": "Active dataset version does not match the current server version.",
        "details": {"current_dataset_version": 2},
    }


def test_add_story_trope_reuses_existing_canonical_trope_by_normalized_text(client: TestClient) -> None:
    upload_dataset(
        client,
        [
            make_row(title="Story One", tropes="§§ wolf comes at night"),
            make_row(title="Story Two"),
        ],
    )
    stories = client.get("/api/stories").json()["items"]
    first_story_id = stories[0]["id"]
    second_story_id = stories[1]["id"]
    existing_trope = client.get(f"/api/stories/{first_story_id}/tropes").json()["items"][0]

    response = client.post(
        f"/api/stories/{second_story_id}/tropes",
        json={
            "expected_story_version": 1,
            "text": "  Wolf Comes At Night  ",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["trope"]["id"] == existing_trope["id"]
    assert body["trope"]["text"] == "wolf comes at night"
    assert body["story_version"] == 2
    assert body["dataset_version"] == 2


def test_validate_story_trope_marks_suggestion_as_human_approved(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Story One")])
    story = client.get("/api/stories").json()["items"][0]

    add_response = client.post(
        f"/api/stories/{story['id']}/tropes",
        json={
            "expected_story_version": 1,
            "text": "Forest mother",
            "origin": "semantic_suggestion",
        },
    )
    assert add_response.status_code == 201
    added = add_response.json()
    assert added["trope"]["origin"] == "semantic_suggestion"
    assert added["trope"]["status"] == "pending"

    validate_response = client.post(
        f"/api/stories/{story['id']}/tropes/{added['trope']['id']}/validate",
        json={"expected_story_version": 2},
    )

    assert validate_response.status_code == 200
    validated = validate_response.json()
    assert validated["story_version"] == 3
    assert validated["dataset_version"] == 3
    assert validated["trope"]["origin"] == "human_approved"
    assert validated["trope"]["status"] == "validated"
    assert validated["queued_job"]["status"] == "queued"

    detail = client.get(f"/api/stories/{story['id']}").json()
    assert detail["version"] == 3
    assert detail["tropes"][0]["origin"] == "human_approved"
    assert detail["tropes"][0]["status"] == "validated"


def test_replace_story_trope_with_typed_text_updates_csv_and_preserves_order(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope\n§§ second trope")])
    story = client.get("/api/stories").json()["items"][0]
    tropes = client.get(f"/api/stories/{story['id']}/tropes").json()["items"]

    response = client.put(
        f"/api/stories/{story['id']}/tropes/{tropes[0]['id']}",
        json={
            "expected_story_version": 1,
            "text": "Edited trope",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["story_version"] == 2
    assert body["dataset_version"] == 2
    assert body["trope"]["text"] == "Edited trope"
    assert body["trope"]["origin"] == "human_entered"
    assert body["trope"]["status"] == "validated"
    assert body["trope"]["position"] == 0
    assert body["queued_job"]["status"] == "queued"
    assert body["queued_job"]["job_type"] == "full_rebuild"

    detail = client.get(f"/api/stories/{story['id']}").json()
    assert detail["fields"][TROPE_FIELD] == "§§ Edited trope\n§§ second trope"
    assert [item["text"] for item in detail["tropes"]] == ["Edited trope", "second trope"]
    assert [item["position"] for item in detail["tropes"]] == [0, 1]


def test_replace_story_trope_reuses_existing_canonical_trope_by_id(client: TestClient) -> None:
    upload_dataset(
        client,
        [
            make_row(title="Story One", tropes="§§ first trope\n§§ second trope"),
            make_row(title="Story Two", tropes="§§ moon bride"),
        ],
    )
    stories = client.get("/api/stories").json()["items"]
    first_story_id = stories[0]["id"]
    second_story_id = stories[1]["id"]
    first_story_tropes = client.get(f"/api/stories/{first_story_id}/tropes").json()["items"]
    replacement_trope = client.get(f"/api/stories/{second_story_id}/tropes").json()["items"][0]

    response = client.put(
        f"/api/stories/{first_story_id}/tropes/{first_story_tropes[0]['id']}",
        json={
            "expected_story_version": 1,
            "trope_id": replacement_trope["id"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trope"]["id"] == replacement_trope["id"]
    assert body["trope"]["text"] == "moon bride"
    assert body["trope"]["story_count"] == 2
    assert body["trope"]["position"] == 0

    detail = client.get(f"/api/stories/{first_story_id}").json()
    assert detail["fields"][TROPE_FIELD] == "§§ moon bride\n§§ second trope"
    assert [item["text"] for item in detail["tropes"]] == ["moon bride", "second trope"]
    assert detail["tropes"][0]["story_count"] == 2


def test_replace_story_trope_rejects_duplicate_assignment(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope\n§§ second trope")])
    story = client.get("/api/stories").json()["items"][0]
    tropes = client.get(f"/api/stories/{story['id']}/tropes").json()["items"]

    response = client.put(
        f"/api/stories/{story['id']}/tropes/{tropes[0]['id']}",
        json={
            "expected_story_version": 1,
            "trope_id": tropes[1]["id"],
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "story_mutation_invalid",
        "message": "Story already has this trope assignment.",
    }


def test_delete_story_trope_removes_assignment_hard(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope")])
    story = client.get("/api/stories").json()["items"][0]
    trope = client.get(f"/api/stories/{story['id']}/tropes").json()["items"][0]

    response = client.request(
        "DELETE",
        f"/api/stories/{story['id']}/tropes/{trope['id']}",
        json={"expected_story_version": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_trope_id"] == trope["id"]
    assert body["story_version"] == 2
    assert body["dataset_version"] == 2
    assert body["queued_job"]["status"] == "queued"

    tropes_payload = client.get(f"/api/stories/{story['id']}/tropes").json()
    assert tropes_payload["story_version"] == 2
    assert tropes_payload["items"] == []

    detail = client.get(f"/api/stories/{story['id']}").json()
    assert detail["fields"][TROPE_FIELD] == ""


def test_story_trope_mutation_returns_409_for_stale_expected_version(client: TestClient) -> None:
    upload_dataset(client, [make_row(title="Story One")])
    story = client.get("/api/stories").json()["items"][0]

    first_response = client.post(
        f"/api/stories/{story['id']}/tropes",
        json={
            "expected_story_version": 1,
            "text": "First trope",
        },
    )
    assert first_response.status_code == 201

    stale_response = client.post(
        f"/api/stories/{story['id']}/tropes",
        json={
            "expected_story_version": 1,
            "text": "Second trope",
        },
    )

    assert stale_response.status_code == 409
    assert stale_response.json() == {
        "code": "story_version_conflict",
        "message": "Story version does not match the current server version.",
        "details": {"current_story_version": 2},
    }
