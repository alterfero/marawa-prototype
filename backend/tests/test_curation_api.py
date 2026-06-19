import csv
import io

from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(*, title: str, tropes: str = "", keywords: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    return row


def build_client(tmp_path, name: str) -> TestClient:
    db_path = tmp_path / name
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=FakeEmbeddingBackend(),
    )
    return TestClient(app)


def upload_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201


def process_next_job(client: TestClient) -> None:
    assert client.app.state.job_runner.process_next_job() is True


def test_near_duplicate_tropes_route_uses_similarity_cache(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-near-duplicates.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [make_row(title="Story One", tropes="§§ first trope\n§§ first trope variant\n§§ second trope")],
        )
        process_next_job(client)

        response = client.get("/api/curation/near-duplicate-tropes")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == FakeEmbeddingBackend.model_name
    assert body["artifact_version"] == 1
    assert body["total"] == 1
    assert body["items"][0]["source_trope"]["text"] == "first trope"
    assert body["items"][0]["target_trope"]["text"] == "first trope variant"
    assert body["items"][0]["source_trope"]["story_count"] == 1
    assert body["items"][0]["target_trope"]["story_count"] == 1
    assert body["items"][0]["similarity_score"] > 0.9


def test_merge_tropes_moves_assignments_deduplicates_links_and_queues_rebuild(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-merge.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", tropes="§§ first trope variant"),
                make_row(title="Story Two", tropes="§§ first trope\n§§ first trope variant"),
            ],
        )
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        story_one_id = stories[0]["id"]
        story_two_id = stories[1]["id"]

        story_one_tropes = client.get(f"/api/stories/{story_one_id}/tropes").json()["items"]
        story_two_tropes = client.get(f"/api/stories/{story_two_id}/tropes").json()["items"]
        source_trope_id = story_one_tropes[0]["id"]
        target_trope_id = next(item["id"] for item in story_two_tropes if item["text"] == "first trope")

        merge_response = client.post(
            "/api/curation/merge-tropes",
            json={
                "source_trope_id": source_trope_id,
                "target_trope_id": target_trope_id,
            },
        )

        story_one_detail = client.get(f"/api/stories/{story_one_id}").json()
        story_two_detail = client.get(f"/api/stories/{story_two_id}").json()
        deleted_source_response = client.delete(f"/api/tropes/{source_trope_id}")

    assert merge_response.status_code == 200
    merge_body = merge_response.json()
    assert merge_body["source_trope_id"] == source_trope_id
    assert merge_body["target_trope_id"] == target_trope_id
    assert merge_body["affected_story_count"] == 2
    assert merge_body["dataset_version"] == 2
    assert merge_body["queued_job"]["status"] == "queued"
    assert merge_body["queued_job"]["job_type"] == "full_rebuild"

    assert story_one_detail["version"] == 2
    assert story_one_detail["fields"][TROPE_FIELD] == "§§ first trope"
    assert [item["text"] for item in story_one_detail["tropes"]] == ["first trope"]
    assert story_one_detail["tropes"][0]["origin"] == "merge"

    assert story_two_detail["version"] == 2
    assert story_two_detail["fields"][TROPE_FIELD] == "§§ first trope"
    assert [item["text"] for item in story_two_detail["tropes"]] == ["first trope"]

    assert deleted_source_response.status_code == 404


def test_validate_merges_applies_batch_and_queues_one_rebuild(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-validate-batch.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", tropes="§§ first trope variant"),
                make_row(title="Story Two", tropes="§§ first trope\n§§ first trope variant"),
                make_row(title="Story Three", tropes="§§ second trope variant"),
                make_row(title="Story Four", tropes="§§ second trope\n§§ second trope variant"),
            ],
        )
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        story_ids = [story["id"] for story in stories]
        story_tropes = {
            story_id: client.get(f"/api/stories/{story_id}/tropes").json()["items"] for story_id in story_ids
        }

        first_variant_id = next(
            trope["id"]
            for trope in story_tropes[story_ids[0]]
            if trope["text"] == "first trope variant"
        )
        first_target_id = next(
            trope["id"]
            for trope in story_tropes[story_ids[1]]
            if trope["text"] == "first trope"
        )
        second_variant_id = next(
            trope["id"]
            for trope in story_tropes[story_ids[2]]
            if trope["text"] == "second trope variant"
        )
        second_target_id = next(
            trope["id"]
            for trope in story_tropes[story_ids[3]]
            if trope["text"] == "second trope"
        )

        validate_response = client.post(
            "/api/curation/validate-merges",
            json={
                "merges": [
                    {
                        "source_trope_id": first_variant_id,
                        "target_trope_id": first_target_id,
                    },
                    {
                        "source_trope_id": second_variant_id,
                        "target_trope_id": second_target_id,
                    },
                ]
            },
        )

        story_details = [client.get(f"/api/stories/{story_id}").json() for story_id in story_ids]
        jobs = client.get("/api/jobs").json()

    assert validate_response.status_code == 200
    body = validate_response.json()
    assert body["merge_count"] == 2
    assert body["affected_story_count"] == 4
    assert body["dataset_version"] == 2
    assert body["queued_job"]["status"] == "queued"
    assert body["queued_job"]["job_type"] == "full_rebuild"
    assert len(body["applied_merges"]) == 2
    assert {merge["source_trope_id"] for merge in body["applied_merges"]} == {first_variant_id, second_variant_id}
    assert len([job for job in jobs if job["job_type"] == "full_rebuild" and job["status"] == "queued"]) == 1

    first_story_detail, second_story_detail, third_story_detail, fourth_story_detail = story_details

    assert first_story_detail["version"] == 2
    assert first_story_detail["fields"][TROPE_FIELD] == "§§ first trope"
    assert [item["text"] for item in first_story_detail["tropes"]] == ["first trope"]

    assert second_story_detail["version"] == 2
    assert second_story_detail["fields"][TROPE_FIELD] == "§§ first trope"
    assert [item["text"] for item in second_story_detail["tropes"]] == ["first trope"]

    assert third_story_detail["version"] == 2
    assert third_story_detail["fields"][TROPE_FIELD] == "§§ second trope"
    assert [item["text"] for item in third_story_detail["tropes"]] == ["second trope"]

    assert fourth_story_detail["version"] == 2
    assert fourth_story_detail["fields"][TROPE_FIELD] == "§§ second trope"
    assert [item["text"] for item in fourth_story_detail["tropes"]] == ["second trope"]


def test_delete_trope_requires_explicit_remove_from_all_stories_and_queues_rebuild(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-delete.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope")])
        process_next_job(client)
        story = client.get("/api/stories").json()["items"][0]
        trope = client.get(f"/api/stories/{story['id']}/tropes").json()["items"][0]

        blocked_response = client.delete(f"/api/tropes/{trope['id']}")
        delete_response = client.delete(f"/api/tropes/{trope['id']}?remove_from_all_stories=true")
        story_detail = client.get(f"/api/stories/{story['id']}").json()

    assert blocked_response.status_code == 409
    assert blocked_response.json()["code"] == "trope_delete_conflict"
    assert "remove_from_all_stories=true" in blocked_response.json()["message"]

    assert delete_response.status_code == 200
    delete_body = delete_response.json()
    assert delete_body["deleted_trope_id"] == trope["id"]
    assert delete_body["affected_story_count"] == 1
    assert delete_body["dataset_version"] == 2
    assert delete_body["queued_job"]["status"] == "queued"
    assert delete_body["queued_job"]["job_type"] == "full_rebuild"

    assert story_detail["version"] == 2
    assert story_detail["fields"][TROPE_FIELD] == ""
    assert story_detail["tropes"] == []


def test_delete_unassigned_trope_succeeds_without_remove_from_all_stories(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-delete-unassigned.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One")])
        process_next_job(client)
        story = client.get("/api/stories").json()["items"][0]

        add_response = client.post(
            f"/api/stories/{story['id']}/tropes",
            json={"expected_story_version": 1, "text": "orphan trope"},
        )
        trope_id = add_response.json()["trope"]["id"]

        remove_assignment_response = client.request(
            "DELETE",
            f"/api/stories/{story['id']}/tropes/{trope_id}",
            json={"expected_story_version": 2},
        )
        assert remove_assignment_response.status_code == 200

        delete_response = client.delete(f"/api/tropes/{trope_id}")

    assert add_response.status_code == 201
    assert delete_response.status_code == 200
    delete_body = delete_response.json()
    assert delete_body["deleted_trope_id"] == trope_id
    assert delete_body["affected_story_count"] == 0
    assert delete_body["dataset_version"] == 4
    assert delete_body["queued_job"]["status"] == "queued"


def test_tropes_route_lists_unused_tropes(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-unused-list.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope")])
        process_next_job(client)
        story = client.get("/api/stories").json()["items"][0]

        add_response = client.post(
            f"/api/stories/{story['id']}/tropes",
            json={"expected_story_version": 1, "text": "orphan trope"},
        )
        trope_id = add_response.json()["trope"]["id"]
        remove_assignment_response = client.request(
            "DELETE",
            f"/api/stories/{story['id']}/tropes/{trope_id}",
            json={"expected_story_version": 2},
        )
        assert remove_assignment_response.status_code == 200

        list_response = client.get("/api/tropes?unused_only=true&q=orphan")

    assert list_response.status_code == 200
    body = list_response.json()
    assert len(body) == 1
    assert body[0]["id"] == trope_id
    assert body[0]["text"] == "orphan trope"
    assert body[0]["story_count"] == 0
