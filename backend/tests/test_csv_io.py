import csv
import io

import pytest
from sqlalchemy import select

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import (
    Dataset,
    DatasetStatus,
    Story,
    StoryKeyword,
    StoryTrope,
    StoryTropeOrigin,
    build_engine,
    build_session_factory,
    initialize_database,
)
from app.db.models import AssignmentStatus
from app.services.csv_io import CSVImportValidationError, export_active_dataset_to_csv_bytes, import_csv_bytes


def make_csv_bytes(rows: list[dict[str, str]], *, fieldnames: list[str] | None = None) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames or CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_session(tmp_path, filename: str = "service.db"):
    db_path = tmp_path / filename
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    return build_session_factory(engine)()


def test_import_csv_rejects_missing_required_legacy_columns(tmp_path) -> None:
    csv_bytes = make_csv_bytes(
        [{"Story title (Eng)": "Story", "Keywords (Eng)": "wolf"}],
        fieldnames=["Story title (Eng)", "Keywords (Eng)"],
    )

    with make_session(tmp_path) as session:
        with pytest.raises(CSVImportValidationError) as exc_info:
            import_csv_bytes(session, csv_bytes, source_filename="missing.csv")

    assert "missing required legacy columns" in str(exc_info.value)
    assert "Entered by" in str(exc_info.value)


def test_import_csv_rejects_extra_columns_to_preserve_exact_export_contract(tmp_path) -> None:
    fieldnames = list(CSV_COLUMNS) + ["Extra Column"]
    csv_bytes = make_csv_bytes(
        [{**{column: "" for column in CSV_COLUMNS}, "Story title (Eng)": "Story", "Extra Column": "keep me"}],
        fieldnames=fieldnames,
    )

    with make_session(tmp_path) as session:
        with pytest.raises(CSVImportValidationError) as exc_info:
            import_csv_bytes(session, csv_bytes, source_filename="extra.csv")

    assert "unsupported extra columns" in str(exc_info.value)
    assert "Exact legacy export is guaranteed only for the canonical legacy header" in str(exc_info.value)


def test_import_csv_rejects_reordered_legacy_header(tmp_path) -> None:
    reordered = list(CSV_COLUMNS)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story"
    csv_bytes = make_csv_bytes([row], fieldnames=reordered)

    with make_session(tmp_path) as session:
        with pytest.raises(CSVImportValidationError) as exc_info:
            import_csv_bytes(session, csv_bytes, source_filename="reordered.csv")

    assert "exact legacy column order" in str(exc_info.value)


def test_import_csv_rejects_malformed_csv_content(tmp_path) -> None:
    header = make_csv_bytes([], fieldnames=CSV_COLUMNS).decode("utf-8-sig")
    malformed_csv = f'{header}"broken title"value'.encode("utf-8")

    with make_session(tmp_path) as session:
        with pytest.raises(CSVImportValidationError) as exc_info:
            import_csv_bytes(session, malformed_csv, source_filename="broken.csv")

    assert "malformed" in str(exc_info.value).lower()


def test_import_csv_creates_active_dataset_and_preserves_story_row_order(tmp_path) -> None:
    row_one = {column: "" for column in CSV_COLUMNS}
    row_one["Story title (Eng)"] = "Story One"
    row_one[KEYWORD_FIELD] = "wolf ; moon"
    row_one[TROPE_FIELD] = "§§ first trope\n§§ second trope"

    row_two = {column: "" for column in CSV_COLUMNS}
    row_two["Story title (Eng)"] = "Story Two"
    row_two[KEYWORD_FIELD] = "river"
    row_two[TROPE_FIELD] = "third trope ; fourth trope"

    with make_session(tmp_path) as session:
        dataset = import_csv_bytes(session, make_csv_bytes([row_one, row_two]), source_filename="stories.csv")

        active_datasets = session.scalars(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE)).all()
        stories = session.scalars(
            select(Story).where(Story.dataset_id == dataset.id).order_by(Story.source_row_number)
        ).all()
        story_tropes = session.scalars(
            select(StoryTrope).join(Story).where(Story.dataset_id == dataset.id).order_by(Story.source_row_number, StoryTrope.position)
        ).all()
        story_keywords = session.scalars(
            select(StoryKeyword).join(Story).where(Story.dataset_id == dataset.id).order_by(Story.source_row_number, StoryKeyword.position)
        ).all()

    assert len(active_datasets) == 1
    assert active_datasets[0].id == dataset.id
    assert [story.source_row_number for story in stories] == [1, 2]
    assert [story.fields_json["Story title (Eng)"] for story in stories] == ["Story One", "Story Two"]
    assert [link.origin for link in story_tropes] == [
        StoryTropeOrigin.CSV_IMPORT,
        StoryTropeOrigin.CSV_IMPORT,
        StoryTropeOrigin.CSV_IMPORT,
        StoryTropeOrigin.CSV_IMPORT,
    ]
    assert [link.status for link in story_tropes] == [
        AssignmentStatus.VALIDATED,
        AssignmentStatus.VALIDATED,
        AssignmentStatus.VALIDATED,
        AssignmentStatus.VALIDATED,
    ]
    assert len(story_keywords) == 3


def test_import_csv_replaces_one_active_dataset(tmp_path) -> None:
    first_row = {column: "" for column in CSV_COLUMNS}
    first_row["Story title (Eng)"] = "First Dataset Story"
    first_row[TROPE_FIELD] = "§§ first trope"

    second_row = {column: "" for column in CSV_COLUMNS}
    second_row["Story title (Eng)"] = "Second Dataset Story"
    second_row[TROPE_FIELD] = "§§ second trope"

    with make_session(tmp_path) as session:
        first_dataset = import_csv_bytes(session, make_csv_bytes([first_row]), source_filename="first.csv")
        second_dataset = import_csv_bytes(session, make_csv_bytes([second_row]), source_filename="second.csv")

        datasets = session.scalars(select(Dataset).order_by(Dataset.created_at, Dataset.id)).all()
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        active_stories = session.scalars(select(Story).where(Story.dataset_id == second_dataset.id)).all()

    assert len(datasets) == 2
    assert first_dataset.id != second_dataset.id
    assert [dataset.status for dataset in datasets] == [DatasetStatus.ARCHIVED, DatasetStatus.ACTIVE]
    assert active_dataset is not None
    assert active_dataset.id == second_dataset.id
    assert [story.fields_json["Story title (Eng)"] for story in active_stories] == ["Second Dataset Story"]


def test_export_csv_uses_exact_legacy_header_and_reconstructs_terms_from_links(tmp_path) -> None:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Canonical Story"
    row[KEYWORD_FIELD] = "wolf ; moon\nwolf"
    row[TROPE_FIELD] = "first trope ; second trope\nfirst trope"

    with make_session(tmp_path) as session:
        import_csv_bytes(session, make_csv_bytes([row]), source_filename="roundtrip.csv")
        exported_bytes = export_active_dataset_to_csv_bytes(session)

    exported_text = exported_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(exported_text))
    rows = list(reader)

    assert reader.fieldnames == CSV_COLUMNS
    assert len(rows) == 1
    assert "id" not in reader.fieldnames
    assert "source_row_number" not in reader.fieldnames
    assert rows[0]["Story title (Eng)"] == "Canonical Story"
    assert rows[0][KEYWORD_FIELD] == "wolf ; moon"
    assert rows[0][TROPE_FIELD] == "§§ first trope\n§§ second trope"


def test_import_export_round_trip_preserves_story_order_and_canonical_term_serialization(tmp_path) -> None:
    first_row = {column: "" for column in CSV_COLUMNS}
    first_row["Story title (Eng)"] = "Story A"
    first_row[KEYWORD_FIELD] = "pandanus ; woman"
    first_row[TROPE_FIELD] = "§§ woman becomes tree"

    second_row = {column: "" for column in CSV_COLUMNS}
    second_row["Story title (Eng)"] = "Story B"
    second_row[KEYWORD_FIELD] = "canoe\ncanoe ; sea"
    second_row[TROPE_FIELD] = "younger sibling wins ; younger sibling wins\neldest loses"

    with make_session(tmp_path) as session:
        import_csv_bytes(session, make_csv_bytes([first_row, second_row]), source_filename="input.csv")
        exported_bytes = export_active_dataset_to_csv_bytes(session)

    reader = csv.DictReader(io.StringIO(exported_bytes.decode("utf-8-sig")))
    rows = list(reader)

    assert [row["Story title (Eng)"] for row in rows] == ["Story A", "Story B"]
    assert rows[0][KEYWORD_FIELD] == "pandanus ; woman"
    assert rows[0][TROPE_FIELD] == "§§ woman becomes tree"
    assert rows[1][KEYWORD_FIELD] == "canoe ; sea"
    assert rows[1][TROPE_FIELD] == "§§ younger sibling wins\n§§ eldest loses"
