import csv
import io

import numpy as np
from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import build_engine, build_session_factory
from app.main import create_app
from tests.auth_helpers import authenticate_admin, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


class ModerateSimilarityEmbeddingBackend(FakeEmbeddingBackend):
    def _vector_for_text(self, text: str) -> np.ndarray:
        if text.strip().lower() == "moderately related trope":
            return np.array([0.7, 0.71414284, 0.0], dtype=np.float32)
        return super()._vector_for_text(text)


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


def build_client(tmp_path, name: str, *, embedding_backend=None) -> TestClient:
    db_path = tmp_path / name
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        embedding_backend=embedding_backend or FakeEmbeddingBackend(),
    )
    return TestClient(app)


def upload_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201


def request_rebuild(client: TestClient) -> None:
    response = client.post("/api/dataset/rebuild")
    assert response.status_code == 200


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
        request_rebuild(client)
        process_next_job(client)

        response = client.get("/api/curation/near-duplicate-tropes")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == FakeEmbeddingBackend.model_name
    assert body["artifact_version"] == 1
    assert body["total"] == 1
    assert body["items"][0]["source_trope"]["text"] == "first trope"
    assert body["items"][0]["source_trope"]["version"] == 1
    assert body["items"][0]["source_trope"]["confirmation_status"] == "unconfirmed"
    assert body["items"][0]["target_trope"]["text"] == "first trope variant"
    assert body["items"][0]["target_trope"]["version"] == 1
    assert body["items"][0]["target_trope"]["confirmation_status"] == "unconfirmed"
    assert body["items"][0]["source_trope"]["story_count"] == 1
    assert body["items"][0]["target_trope"]["story_count"] == 1
    assert body["items"][0]["similarity_score"] > 0.9


def test_near_duplicate_tropes_places_the_unconfirmed_trope_on_the_left(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-near-duplicates-orientation.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [make_row(title="Story One", tropes="§§ first trope\n§§ first trope variant\n§§ second trope")],
        )
        request_rebuild(client)
        process_next_job(client)

        initial_pair = client.get("/api/curation/near-duplicate-tropes").json()["items"][0]
        confirmation_response = client.put(
            f"/api/tropes/{initial_pair['source_trope']['id']}/confirmation",
            json={
                "expected_trope_version": initial_pair["source_trope"]["version"],
                "confirmation_status": "canonical",
            },
        )
        response = client.get("/api/curation/near-duplicate-tropes")

    assert confirmation_response.status_code == 200
    assert response.status_code == 200
    pair = response.json()["items"][0]
    assert pair["source_trope"]["text"] == "first trope variant"
    assert pair["source_trope"]["confirmation_status"] == "unconfirmed"
    assert pair["target_trope"]["text"] == "first trope"
    assert pair["target_trope"]["confirmation_status"] == "canonical"


def test_similar_unconfirmed_tropes_filters_canonical_candidates_by_default_and_can_include_them(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(
        tmp_path,
        "curation-similar-unconfirmed.db",
        embedding_backend=ModerateSimilarityEmbeddingBackend(),
    ) as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [make_row(title="Story One", tropes="§§ first trope\n§§ moderately related trope\n§§ second trope")],
        )
        request_rebuild(client)
        process_next_job(client)

        tropes = client.get("/api/tropes").json()
        source_trope = next(item for item in tropes if item["text"] == "first trope")
        related_response = client.get(f"/api/curation/tropes/{source_trope['id']}/similar-unconfirmed")
        strict_response = client.get(
            f"/api/curation/tropes/{source_trope['id']}/similar-unconfirmed?minimum_similarity=0.8"
        )

        related_item = related_response.json()["items"][0]
        canonicalize_response = client.put(
            f"/api/tropes/{related_item['id']}/confirmation",
            json={
                "expected_trope_version": related_item["version"],
                "confirmation_status": "canonical",
            },
        )
        after_canonicalize_response = client.get(f"/api/curation/tropes/{source_trope['id']}/similar-unconfirmed")
        include_canonical_response = client.get(
            f"/api/curation/tropes/{source_trope['id']}/similar-unconfirmed?include_canonical=true"
        )

    assert related_response.status_code == 200
    related_body = related_response.json()
    assert related_body["artifact_version"] == 1
    assert related_body["minimum_similarity"] == 0.6
    assert related_body["total"] == 1
    assert related_body["items"][0]["text"] == "moderately related trope"
    assert related_body["items"][0]["confirmation_status"] == "unconfirmed"
    assert 0.6 <= related_body["items"][0]["similarity_score"] < 0.8

    assert strict_response.status_code == 200
    assert strict_response.json()["items"] == []

    assert canonicalize_response.status_code == 200
    assert after_canonicalize_response.status_code == 200
    assert after_canonicalize_response.json()["items"] == []
    assert include_canonical_response.status_code == 200
    assert include_canonical_response.json()["total"] == 1
    assert include_canonical_response.json()["items"][0]["text"] == "moderately related trope"
    assert include_canonical_response.json()["items"][0]["confirmation_status"] == "canonical"


def test_canonicalize_tropes_route_marks_both_canonical_and_hides_fully_canonical_pair(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-canonicalize-tropes.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [make_row(title="Story One", tropes="§§ first trope\n§§ first trope variant\n§§ second trope")],
        )
        request_rebuild(client)
        process_next_job(client)

        pairs_response = client.get("/api/curation/near-duplicate-tropes")
        pair = pairs_response.json()["items"][0]

        canonicalize_response = client.post(
            "/api/curation/canonicalize-tropes",
            json={
                "tropes": [
                    {
                        "trope_id": pair["source_trope"]["id"],
                        "expected_trope_version": pair["source_trope"]["version"],
                    },
                    {
                        "trope_id": pair["target_trope"]["id"],
                        "expected_trope_version": pair["target_trope"]["version"],
                    },
                ]
            },
        )

        filtered_pairs_response = client.get("/api/curation/near-duplicate-tropes")

    assert canonicalize_response.status_code == 200
    canonicalize_body = canonicalize_response.json()
    assert [item["confirmation_status"] for item in canonicalize_body["tropes"]] == ["canonical", "canonical"]
    assert [item["version"] for item in canonicalize_body["tropes"]] == [2, 2]

    assert filtered_pairs_response.status_code == 200
    filtered_body = filtered_pairs_response.json()
    assert filtered_body["total"] == 0
    assert filtered_body["items"] == []


def test_merge_tropes_moves_assignments_and_deduplicates_links(monkeypatch, tmp_path) -> None:
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
        request_rebuild(client)
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
    assert merge_body["queued_job"] is None

    assert story_one_detail["version"] == 2
    assert story_one_detail["fields"][TROPE_FIELD] == "§§ first trope"
    assert [item["text"] for item in story_one_detail["tropes"]] == ["first trope"]
    assert story_one_detail["tropes"][0]["origin"] == "merge"

    assert story_two_detail["version"] == 2
    assert story_two_detail["fields"][TROPE_FIELD] == "§§ first trope"
    assert [item["text"] for item in story_two_detail["tropes"]] == ["first trope"]

    assert deleted_source_response.status_code == 404


def test_validate_merges_applies_batch_without_queueing_rebuild(monkeypatch, tmp_path) -> None:
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
        request_rebuild(client)
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
    assert body["queued_job"] is None
    assert len(body["applied_merges"]) == 2
    assert {merge["source_trope_id"] for merge in body["applied_merges"]} == {first_variant_id, second_variant_id}
    assert len([job for job in jobs if job["job_type"] == "full_rebuild"]) == 1

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


def test_delete_trope_requires_explicit_remove_from_all_stories(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-delete.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope")])
        request_rebuild(client)
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
    assert delete_body["queued_job"] is None

    assert story_detail["version"] == 2
    assert story_detail["fields"][TROPE_FIELD] == ""
    assert story_detail["tropes"] == []


def test_delete_unassigned_trope_succeeds_without_remove_from_all_stories(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-delete-unassigned.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One")])
        request_rebuild(client)
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
    assert delete_body["queued_job"] is None


def test_delete_all_unused_tropes_removes_every_unassigned_trope(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-delete-all-unused.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", tropes="§§ retained trope")])
        request_rebuild(client)
        process_next_job(client)
        story = client.get("/api/stories").json()["items"][0]

        first_orphan = client.post(
            f"/api/stories/{story['id']}/tropes",
            json={"expected_story_version": 1, "text": "first orphan"},
        )
        first_orphan_id = first_orphan.json()["trope"]["id"]
        remove_first_orphan = client.request(
            "DELETE",
            f"/api/stories/{story['id']}/tropes/{first_orphan_id}",
            json={"expected_story_version": 2},
        )
        second_orphan = client.post(
            f"/api/stories/{story['id']}/tropes",
            json={"expected_story_version": 3, "text": "second orphan"},
        )
        second_orphan_id = second_orphan.json()["trope"]["id"]
        remove_second_orphan = client.request(
            "DELETE",
            f"/api/stories/{story['id']}/tropes/{second_orphan_id}",
            json={"expected_story_version": 4},
        )

        delete_response = client.delete("/api/curation/unused-tropes")
        remaining_tropes = client.get("/api/tropes").json()
        repeat_delete_response = client.delete("/api/curation/unused-tropes")

    assert first_orphan.status_code == 201
    assert remove_first_orphan.status_code == 200
    assert second_orphan.status_code == 201
    assert remove_second_orphan.status_code == 200
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_trope_count"] == 2
    assert delete_response.json()["queued_job"] is None
    assert [trope["text"] for trope in remaining_tropes] == ["retained trope"]
    assert repeat_delete_response.status_code == 200
    assert repeat_delete_response.json()["deleted_trope_count"] == 0


def test_tropes_route_lists_unused_tropes(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "curation-unused-list.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", tropes="§§ first trope")])
        request_rebuild(client)
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


def test_keyword_curation_similarity_filters_canonical_candidates_and_near_duplicate_pairs(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "keyword-curation-similarity.db") as client:
        authenticate_admin(client)
        upload_dataset(client, [make_row(title="Story One", keywords="wolf ; moon ; river")])
        request_rebuild(client)
        process_next_job(client)

        keywords = client.get("/api/keywords").json()
        wolf = next(keyword for keyword in keywords if keyword["text"] == "wolf")
        moon = next(keyword for keyword in keywords if keyword["text"] == "moon")

        pairs_response = client.get("/api/curation/near-duplicate-keywords")
        similar_response = client.get(f"/api/curation/keywords/{wolf['id']}/similar-unconfirmed")
        canonicalize_response = client.put(
            f"/api/keywords/{moon['id']}/confirmation",
            json={
                "expected_keyword_version": moon["version"],
                "confirmation_status": "canonical",
            },
        )
        filtered_response = client.get(f"/api/curation/keywords/{wolf['id']}/similar-unconfirmed")
        include_canonical_response = client.get(
            f"/api/curation/keywords/{wolf['id']}/similar-unconfirmed?include_canonical=true"
        )
        oriented_pairs_response = client.get("/api/curation/near-duplicate-keywords")

    assert pairs_response.status_code == 200
    pairs_body = pairs_response.json()
    assert pairs_body["artifact_version"] == 1
    assert pairs_body["model_name"] == FakeEmbeddingBackend.model_name
    assert pairs_body["total"] == 1
    first_pair = pairs_body["items"][0]
    assert {first_pair["source_keyword"]["text"], first_pair["target_keyword"]["text"]} == {"wolf", "moon"}
    assert first_pair["similarity_score"] > 0.9

    assert similar_response.status_code == 200
    similar_body = similar_response.json()
    assert similar_body["artifact_version"] == 1
    assert similar_body["items"][0]["text"] == "moon"
    assert similar_body["items"][0]["confirmation_status"] == "unconfirmed"

    assert canonicalize_response.status_code == 200
    assert filtered_response.status_code == 200
    assert filtered_response.json()["items"] == []
    assert include_canonical_response.status_code == 200
    assert include_canonical_response.json()["items"][0]["text"] == "moon"
    assert include_canonical_response.json()["items"][0]["confirmation_status"] == "canonical"

    assert oriented_pairs_response.status_code == 200
    oriented_pair = oriented_pairs_response.json()["items"][0]
    assert oriented_pair["source_keyword"]["text"] == "wolf"
    assert oriented_pair["source_keyword"]["confirmation_status"] == "unconfirmed"
    assert oriented_pair["target_keyword"]["text"] == "moon"
    assert oriented_pair["target_keyword"]["confirmation_status"] == "canonical"


def test_keyword_curation_merges_assignments_and_deletes_unused_keywords(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    with build_client(tmp_path, "keyword-curation-merge.db") as client:
        authenticate_admin(client)
        upload_dataset(
            client,
            [
                make_row(title="Story One", keywords="moon"),
                make_row(title="Story Two", keywords="wolf ; moon"),
            ],
        )
        request_rebuild(client)
        process_next_job(client)

        stories = client.get("/api/stories").json()["items"]
        story_one_id = stories[0]["id"]
        story_two_id = stories[1]["id"]
        keywords = client.get("/api/keywords").json()
        source = next(keyword for keyword in keywords if keyword["text"] == "moon")
        target = next(keyword for keyword in keywords if keyword["text"] == "wolf")

        merge_response = client.post(
            "/api/curation/merge-keywords",
            json={
                "source_keyword_id": source["id"],
                "target_keyword_id": target["id"],
            },
        )
        story_one_detail = client.get(f"/api/stories/{story_one_id}").json()
        story_two_detail = client.get(f"/api/stories/{story_two_id}").json()
        missing_source_response = client.get(f"/api/keywords/{source['id']}")

        create_unused_response = client.post("/api/keywords", json={"text": "orphan keyword"})
        delete_unused_response = client.delete("/api/curation/unused-keywords")
        remaining_keywords = client.get("/api/keywords").json()

    assert merge_response.status_code == 200
    merge_body = merge_response.json()
    assert merge_body["source_keyword_id"] == source["id"]
    assert merge_body["target_keyword_id"] == target["id"]
    assert merge_body["affected_story_count"] == 2
    assert merge_body["dataset_version"] == 2
    assert merge_body["queued_job"] is None

    assert story_one_detail["version"] == 2
    assert story_one_detail["fields"][KEYWORD_FIELD] == "wolf"
    assert [keyword["text"] for keyword in story_one_detail["keywords"]] == ["wolf"]
    assert story_two_detail["version"] == 2
    assert story_two_detail["fields"][KEYWORD_FIELD] == "wolf"
    assert [keyword["text"] for keyword in story_two_detail["keywords"]] == ["wolf"]
    assert missing_source_response.status_code == 404

    assert create_unused_response.status_code == 200
    assert create_unused_response.json()["created"] is True
    assert delete_unused_response.status_code == 200
    assert delete_unused_response.json()["deleted_keyword_count"] == 1
    assert delete_unused_response.json()["queued_job"] is None
    assert [keyword["text"] for keyword in remaining_keywords] == ["wolf"]
