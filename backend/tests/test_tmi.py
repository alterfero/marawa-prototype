from pathlib import Path
import csv
import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from app.core.tmi import build_tmi_catalogue
from app.db import UserRole, build_engine, build_session_factory
from app.main import create_app
from app.services.tmi import get_tmi_catalogue
from app.services import tmi as tmi_service
from tests.auth_helpers import authenticate_admin, authenticate_role, configure_auth_env
from tests.search_fakes import FakeEmbeddingBackend


FIELDS = ["code", "[sorting field]", "1st ed.", "chapter", "division1", "division2",
          "division3", 'section ("tens")', "MOTIF", "bibliographies"]


def catalogue_csv(*entries):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for entry in entries:
        writer.writerow({"chapter": "A. Mythological motifs.", "division1": "A0–A99. Creator.",
                         'section ("tens")': "A10. Nature of the creator.", **entry})
    return stream.getvalue()


def test_preserves_bibliographies_duplicates_and_missing_intermediate_codes():
    bibliography = 'Eskimo: Nelson 454; Koryak: Jochelson 218.\n*Quoted "source" — preserved.'
    graph = build_tmi_catalogue(catalogue_csv(
        {"code": "A13", "MOTIF": "A13. Animal as creator.", "bibliographies": bibliography},
        {"code": "A13.2.1", "MOTIF": "A13.2.1. Raven as creator."},
        {"code": "A13", "MOTIF": "A13. Another description.", "bibliographies": "Another source."},
        {"code": "A13", "MOTIF": "A13. Animal as creator.", "bibliographies": bibliography},
    ))
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["A13"]["text"] == "Animal as creator."
    assert nodes["A13"]["alternate_texts"] == ["Another description."]
    assert nodes["A13"]["source_rows"] == [2, 4, 5]
    assert nodes["A13"]["bibliographies"] == [bibliography, "Another source."]
    assert nodes["A13.2.1"]["bibliographies"] == []
    assert nodes["A13.2"]["kind"] == "gap"
    assert nodes["A13.2"]["parent_id"] == "A13"
    assert nodes["A13.2.1"]["parent_id"] == "A13.2"
    assert graph["stats"]["source_rows"] == 4
    assert graph["stats"]["trope_count"] == 2
    assert graph["stats"]["conflicting_codes"] == 1


def test_csv_headings_define_placements_without_self_links_or_overwriting_entries():
    hierarchy = {"chapter": "D. Magic.", "division1": "D0–D699. Transformation.",
                 "division2": "D300–D399. Transformation: animal to person.",
                 'section ("tens")': "D370. Transformation: fish to man."}
    graph = build_tmi_catalogue(catalogue_csv(
        {**hierarchy, "code": "D370", "MOTIF": "D370. Full description from the entry."},
        {**hierarchy, "code": "D373", "MOTIF": "D373. Transformation: eel to person.",
         "bibliographies": 'Fb "ål" III 119Oa. Tonga: Gifford 182; New Hebrides: Codrington 375; Tuamotu: Stimson MS (z-G. 3/1295).'},
        {**hierarchy, "code": "D370.1", "MOTIF": "D370.1. Fish cleaned by girl becomes man."},
    ))
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["D373"]["parent_id"] == "D370"
    assert nodes["D370"]["parent_id"] == "D300-D399"
    assert nodes["D300-D399"]["parent_id"] == "D0-D699"
    assert nodes["D370.1"]["parent_id"] == "D370"
    assert nodes["D"]["kind"] == "chapter"
    assert nodes["D"]["text"] == "Magic"
    assert nodes["D370"]["text"] == "Full description from the entry."
    assert nodes["D370"]["kind"] == "trope"


def test_first_edition_rows_remain_distinct_from_revised_codes():
    graph = build_tmi_catalogue(catalogue_csv(
        {"code": "A13", "MOTIF": "A13. Revised description."},
        {"1st ed.": "A13", "MOTIF": "[First Edition: A13. Earlier description.]", "bibliographies": "Earlier source."},
    ))
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["A13"]["edition"] == "revised"
    assert nodes["first-ed:A13"]["edition"] == "first"
    assert nodes["first-ed:A13"]["code"] == "A13"
    assert nodes["first-ed:A13"]["parent_id"] == "A10"
    assert nodes["first-ed:A13"]["bibliographies"] == ["Earlier source."]
    assert graph["stats"]["trope_count"] == 2
    assert graph["stats"]["first_edition_count"] == 1


def test_service_loads_only_mellmann_csv(monkeypatch, tmp_path):
    raw = catalogue_csv({"code": "A13", "MOTIF": "A13. CSV description.", "bibliographies": "CSV source."}).encode()
    (tmp_path / "tmi.csv").write_bytes(raw)
    (tmp_path / "tmi.txt").write_text("This obsolete file must never be parsed.")
    monkeypatch.setattr(tmi_service, "TMI_DIRECTORY", tmp_path)
    graph = get_tmi_catalogue()
    assert graph["source"]["filename"] == "tmi.csv"
    assert graph["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert graph["source"]["url"] == "https://github.com/KatjaMellmann/TMI_as_CSV"
    assert graph["stats"]["trope_count"] == 1
    assert graph["stats"]["chapter_count"] == 1
    assert next(node for node in graph["nodes"] if node["id"] == "A13")["bibliographies"] == ["CSV source."]


def test_full_catalogue_preserves_every_csv_record_and_bibliography():
    graph = get_tmi_catalogue()
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert graph["stats"]["source_rows"] == 46299
    assert graph["stats"]["trope_count"] == 46299
    assert graph["stats"]["first_edition_count"] == 61
    assert len(graph["roots"]) == 23
    assert sorted(row for node in nodes.values() for row in node["source_rows"]) == list(range(2, 46301))
    with (tmi_service.TMI_DIRECTORY / "tmi.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for node in nodes.values():
        for row_number in node["source_rows"]:
            row = rows[row_number - 2]
            assert node["code"] == (row["code"] or row["1st ed."])
            assert row["MOTIF"] in node["source_texts"]
            assert node["bibliographies"] == ([row["bibliographies"]] if row["bibliographies"] else [])
        visited = set()
        current = node
        while current["parent_id"] is not None:
            assert current["id"] not in visited, f"Cycle at {node['id']}"
            visited.add(current["id"])
            current = nodes[current["parent_id"]]
        assert current["id"] in graph["roots"]
    assert nodes["D373"]["parent_id"] == "D370"
    assert nodes["A1"]["parent_id"] == "A0"
    assert nodes["M202.1[.1]"]["parent_id"] == "M202.1"
    assert nodes["N591[b]"]["parent_id"] == "N590"
    assert nodes["Z64a"]["parent_id"] == "Z60"
    assert nodes["N591[b]"]["text"] == "Treasure from striking animal or person and disenchanting him."
    assert nodes["T317.0.1"]["text"].startswith("Life shortened by incontinence.")


@pytest.mark.parametrize("text", ["code,MOTIF\nA1,Creator", catalogue_csv({"MOTIF": "No code"}),
                                  catalogue_csv({"code": "A1", "MOTIF": ""})])
def test_invalid_source_is_rejected_instead_of_silently_losing_rows(text):
    with pytest.raises(ValueError):
        build_tmi_catalogue(text)


@pytest.mark.parametrize("role", [None, UserRole.GUEST, UserRole.CONTRIBUTOR, UserRole.ADMIN])
def test_tmi_endpoint_is_admin_only_without_an_active_dataset(monkeypatch, tmp_path: Path, role):
    configure_auth_env(monkeypatch)
    engine = build_engine(f"sqlite:///{tmp_path / 'tmi.db'}")
    app = create_app(db_engine=engine, session_factory=build_session_factory(engine),
                     job_runner_enabled=False, embedding_backend=FakeEmbeddingBackend())
    with TestClient(app) as client:
        if role == UserRole.ADMIN:
            authenticate_admin(client)
        elif role:
            with TestClient(app) as admin:
                authenticate_admin(admin)
                authenticate_role(admin, client, email=f"{role.value}@example.com", display_name="Reader",
                                  role=role, password="reader-password")
        response = client.get("/api/tmi/graph")
        assert response.status_code == (200 if role == UserRole.ADMIN else 403 if role else 401)
        if role == UserRole.ADMIN:
            assert response.json()["stats"]["trope_count"] == 46299
            assert response.headers["cache-control"] == "private, no-store"
