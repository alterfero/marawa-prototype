import csv
import io
import json

import pytest
from sqlalchemy import select

from app.core.csv_schema import (
    CSV_COLUMNS,
    FULL_EXPORT_COLUMNS,
    KEYWORD_FIELD,
    MARAWA_STORY_METADATA_FIELD,
    MARAWA_TERM_CATALOG_FIELD,
    THEME_FIELD,
    TROPE_FIELD,
    TROPE_PROPOSAL_FIELD,
)
from app.db import (
    Dataset,
    DatasetStatus,
    Keyword,
    Story,
    StoryCompleteness,
    StoryKeyword,
    StoryTheme,
    StoryTrope,
    StoryTropeOrigin,
    TermReviewStatus,
    ThemeConfirmationStatus,
    Trope,
    build_engine,
    build_session_factory,
    initialize_database,
)
from app.db.models import AssignmentStatus, KeywordConfirmationStatus, Theme, TropeConfirmationStatus
from app.services.csv_io import CSVImportValidationError, _load_csv_rows, export_active_dataset_to_csv_bytes, import_csv_bytes


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


def activate_dataset(session, dataset: Dataset) -> None:
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is not None and active_dataset.id != dataset.id:
        active_dataset.status = DatasetStatus.ARCHIVED
    dataset.status = DatasetStatus.ACTIVE
    session.commit()


def make_current_template_fieldnames() -> list[str]:
    return [
        "Entered by",
        "Source first or second hand",
        "Source",
        "pages",
        "Other source",
        "URL ?",
        "territory",
        "lg group",
        "original language",
        "lg of publication",
        "bilingual?",
        "storyteller",
        "date of recording ",
        "place of recording",
        "space coord",
        "editor",
        "translator",
        "Story title (Eng)",
        "Story title (French)",
        "Story title (other)",
        "1-sentence summary",
        "Abstracts : AI or Human ?",
        "Abstract (Eng)",
        "Abstract (Fr)",
        "Keywords (Eng)",
        "Motifs (Eng)",
        "motifs inhabituels à une version",
        "Motifs validés ",
        "species",
        "non-human",
        "placenames",
        "named characters",
        "external link",
        "description of link",
        "Connection to other stories",
        "Megamotifs",
        "Thème ",
        "Conte type",
        "Autres infos données dans le texte, pour la fiche conte ",
        "ATU conte-type(AI ?)",
        "ATU motifs (AI?)",
        "motifs Pacifique  ?",
    ]


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


def test_import_csv_accepts_extra_columns_and_reordered_legacy_fields(tmp_path) -> None:
    fieldnames = list(CSV_COLUMNS)
    fieldnames[0], fieldnames[1] = fieldnames[1], fieldnames[0]
    fieldnames.append("Extra Column")
    csv_bytes = make_csv_bytes(
        [{**{column: "" for column in CSV_COLUMNS}, "Story title (Eng)": "Story", "Extra Column": "keep me"}],
        fieldnames=fieldnames,
    )

    with make_session(tmp_path) as session:
        dataset = import_csv_bytes(session, csv_bytes, source_filename="extra.csv")
        activate_dataset(session, dataset)
        story = session.scalar(select(Story).where(Story.dataset_id == dataset.id))
        exported_bytes = export_active_dataset_to_csv_bytes(session)

    assert story is not None
    assert story.fields_json["Story title (Eng)"] == "Story"
    reader = csv.DictReader(io.StringIO(exported_bytes.decode("utf-8-sig")))
    assert reader.fieldnames == CSV_COLUMNS
    assert "Extra Column" not in reader.fieldnames


def test_theme_import_is_normalized_and_exported_through_legacy_theme_column(tmp_path) -> None:
    csv_bytes = make_csv_bytes(
        [
            {
                **{column: "" for column in CSV_COLUMNS},
                "Story title (Eng)": "First story",
                THEME_FIELD: "§§ Creation\n§§ Ocean",
            },
            {
                **{column: "" for column in CSV_COLUMNS},
                "Story title (Eng)": "Second story",
                THEME_FIELD: "creation",
            },
        ]
    )

    with make_session(tmp_path) as session:
        dataset = import_csv_bytes(session, csv_bytes, source_filename="themes.csv")
        activate_dataset(session, dataset)
        themes = session.scalars(select(Theme).order_by(Theme.text)).all()
        links = session.scalars(select(StoryTheme)).all()
        exported = export_active_dataset_to_csv_bytes(session)

    assert [(theme.text, theme.cached_story_count) for theme in themes] == [("Creation", 2), ("Ocean", 1)]
    assert len(links) == 3
    reader = csv.DictReader(io.StringIO(exported.decode("utf-8-sig")))
    exported_rows = list(reader)
    assert reader.fieldnames == CSV_COLUMNS
    assert exported_rows[0][THEME_FIELD] == "§§ Creation\n§§ Ocean"
    assert exported_rows[1][THEME_FIELD] == "§§ Creation"


def test_import_csv_maps_current_template_aliases_back_to_legacy_export_fields(tmp_path) -> None:
    fieldnames = make_current_template_fieldnames()
    row = {column: "" for column in fieldnames}
    row["Story title (Eng)"] = "Template Story"
    row["Abstracts : AI or Human ?"] = "Human"
    row[KEYWORD_FIELD] = "wolf ; moon"
    row[TROPE_FIELD] = "§§ first trope\n§§ second trope"
    row["motifs inhabituels à une version"] = "new trope idea"
    row["Motifs validés "] = "ok"
    row["motifs Pacifique  ?"] = "yes"
    csv_bytes = make_csv_bytes([row], fieldnames=fieldnames)

    with make_session(tmp_path) as session:
        dataset = import_csv_bytes(session, csv_bytes, source_filename="template.csv")
        activate_dataset(session, dataset)
        story = session.scalar(select(Story).where(Story.dataset_id == dataset.id))
        exported_bytes = export_active_dataset_to_csv_bytes(session)

    assert story is not None
    assert story.fields_json["Story title (Eng)"] == "Template Story"
    assert story.fields_json[TROPE_PROPOSAL_FIELD] == "new trope idea"
    assert "Abstracts : AI or Human ?" not in story.fields_json

    reader = csv.DictReader(io.StringIO(exported_bytes.decode("utf-8-sig")))
    rows = list(reader)
    assert reader.fieldnames == CSV_COLUMNS
    assert rows[0][TROPE_PROPOSAL_FIELD] == "new trope idea"
    assert "Abstracts : AI or Human ?" not in reader.fieldnames


def test_import_csv_rejects_duplicate_headers_that_map_to_the_same_legacy_field(tmp_path) -> None:
    fieldnames = list(CSV_COLUMNS) + ["motifs inhabituels à une version"]
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story"
    row["motifs inhabituels à une version"] = "duplicate"
    csv_bytes = make_csv_bytes([row], fieldnames=fieldnames)

    with make_session(tmp_path) as session:
        with pytest.raises(CSVImportValidationError) as exc_info:
            import_csv_bytes(session, csv_bytes, source_filename="duplicate.csv")

    assert "maps multiple header columns to the same legacy field" in str(exc_info.value)
    assert TROPE_PROPOSAL_FIELD in str(exc_info.value)


def test_import_csv_rejects_malformed_csv_content(tmp_path) -> None:
    header = make_csv_bytes([], fieldnames=CSV_COLUMNS).decode("utf-8-sig")
    malformed_csv = f'{header}"broken title"value'.encode("utf-8")

    with make_session(tmp_path) as session:
        with pytest.raises(CSVImportValidationError) as exc_info:
            import_csv_bytes(session, malformed_csv, source_filename="broken.csv")

    assert "malformed" in str(exc_info.value).lower()


def test_full_csv_accepts_a_term_catalog_larger_than_the_default_csv_field_limit() -> None:
    row = {column: "" for column in FULL_EXPORT_COLUMNS}
    row["Story title (Eng)"] = "Large full export"
    row[MARAWA_STORY_METADATA_FIELD] = json.dumps(
        {
            "schema_version": 1,
            "completeness": "incomplete",
            "source_row_number": 1,
            "trope_assignments": [],
        }
    )
    row[MARAWA_TERM_CATALOG_FIELD] = json.dumps(
        {
            "schema_version": 1,
            "tropes": [
                {
                    "text": f"unused trope {index} {'x' * 100}",
                    "confirmation_status": "unconfirmed",
                    "review_status": "approved",
                }
                for index in range(1000)
            ],
            "themes": [],
            "keywords": [],
        }
    )
    assert len(row[MARAWA_TERM_CATALOG_FIELD]) > csv.field_size_limit()

    rows, is_full_export = _load_csv_rows(make_csv_bytes([row], fieldnames=FULL_EXPORT_COLUMNS))

    assert is_full_export is True
    assert len(rows) == 1
    assert len(rows[0].term_catalog["tropes"]) == 1000


def test_import_csv_creates_staged_dataset_and_preserves_story_row_order(tmp_path) -> None:
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
        staged_datasets = session.scalars(select(Dataset).where(Dataset.status == DatasetStatus.STAGED)).all()
        stories = session.scalars(
            select(Story).where(Story.dataset_id == dataset.id).order_by(Story.source_row_number)
        ).all()
        story_tropes = session.scalars(
            select(StoryTrope).join(Story).where(Story.dataset_id == dataset.id).order_by(Story.source_row_number, StoryTrope.position)
        ).all()
        story_keywords = session.scalars(
            select(StoryKeyword).join(Story).where(Story.dataset_id == dataset.id).order_by(Story.source_row_number, StoryKeyword.position)
        ).all()

    assert active_datasets == []
    assert len(staged_datasets) == 1
    assert staged_datasets[0].id == dataset.id
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


def test_import_csv_keeps_the_existing_active_dataset_until_promotion(tmp_path) -> None:
    first_row = {column: "" for column in CSV_COLUMNS}
    first_row["Story title (Eng)"] = "First Dataset Story"
    first_row[TROPE_FIELD] = "§§ first trope"

    second_row = {column: "" for column in CSV_COLUMNS}
    second_row["Story title (Eng)"] = "Second Dataset Story"
    second_row[TROPE_FIELD] = "§§ second trope"

    with make_session(tmp_path) as session:
        first_dataset = import_csv_bytes(session, make_csv_bytes([first_row]), source_filename="first.csv")
        activate_dataset(session, first_dataset)
        second_dataset = import_csv_bytes(session, make_csv_bytes([second_row]), source_filename="second.csv")

        datasets = session.scalars(select(Dataset).order_by(Dataset.created_at, Dataset.id)).all()
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        staged_dataset = session.scalar(select(Dataset).where(Dataset.id == second_dataset.id))
        active_stories = session.scalars(select(Story).where(Story.dataset_id == first_dataset.id)).all()
        staged_stories = session.scalars(select(Story).where(Story.dataset_id == second_dataset.id)).all()

    assert len(datasets) == 2
    assert first_dataset.id != second_dataset.id
    assert [dataset.status for dataset in datasets] == [DatasetStatus.ACTIVE, DatasetStatus.STAGED]
    assert active_dataset is not None
    assert staged_dataset is not None
    assert active_dataset.id == first_dataset.id
    assert staged_dataset.id == second_dataset.id
    assert [story.fields_json["Story title (Eng)"] for story in active_stories] == ["First Dataset Story"]
    assert [story.fields_json["Story title (Eng)"] for story in staged_stories] == ["Second Dataset Story"]


def test_export_csv_uses_exact_legacy_header_and_reconstructs_terms_from_links(tmp_path) -> None:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Canonical Story"
    row[KEYWORD_FIELD] = "wolf ; moon\nwolf"
    row[TROPE_FIELD] = "first trope ; second trope\nfirst trope"

    with make_session(tmp_path) as session:
        dataset = import_csv_bytes(session, make_csv_bytes([row]), source_filename="roundtrip.csv")
        activate_dataset(session, dataset)
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


def test_full_export_round_trips_story_and_term_metadata(tmp_path) -> None:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Lossless Story"
    row[KEYWORD_FIELD] = "wolf ; moon"
    row[TROPE_FIELD] = "§§ first trope\n§§ second trope"
    row[THEME_FIELD] = "§§ Creation\n§§ Ocean"

    with make_session(tmp_path, "full-export-source.db") as session:
        dataset = import_csv_bytes(session, make_csv_bytes([row]), source_filename="source.csv")
        activate_dataset(session, dataset)
        story = session.scalar(select(Story).where(Story.dataset_id == dataset.id))
        assert story is not None
        story.completeness = StoryCompleteness.COMPLETE
        trope_links = sorted(story.trope_links, key=lambda link: link.position)
        trope_links[0].trope.confirmation_status = TropeConfirmationStatus.CANONICAL
        trope_links[0].trope.review_status = TermReviewStatus.REJECTED
        trope_links[1].origin = StoryTropeOrigin.SEMANTIC_SUGGESTION
        trope_links[1].status = AssignmentStatus.PENDING
        keyword_links = sorted(story.keyword_links, key=lambda link: link.position)
        keyword_links[0].keyword.confirmation_status = KeywordConfirmationStatus.CANONICAL
        keyword_links[0].keyword.review_status = TermReviewStatus.PENDING_REVIEW
        theme_links = sorted(story.theme_links, key=lambda link: link.position)
        theme_links[0].theme.confirmation_status = ThemeConfirmationStatus.CANONICAL
        session.add_all(
            [
                Trope(
                    dataset_id=dataset.id,
                    text="unused canonical trope",
                    confirmation_status=TropeConfirmationStatus.CANONICAL,
                    review_status=TermReviewStatus.PENDING_REVIEW,
                ),
                Theme(
                    dataset_id=dataset.id,
                    text="unused canonical theme",
                    confirmation_status=ThemeConfirmationStatus.CANONICAL,
                ),
                Keyword(
                    dataset_id=dataset.id,
                    text="unused canonical keyword",
                    confirmation_status=KeywordConfirmationStatus.CANONICAL,
                    review_status=TermReviewStatus.REJECTED,
                ),
            ]
        )
        session.commit()
        exported = export_active_dataset_to_csv_bytes(session, include_marawa_metadata=True)

    reader = csv.DictReader(io.StringIO(exported.decode("utf-8-sig")))
    exported_rows = list(reader)
    story_metadata = json.loads(exported_rows[0][MARAWA_STORY_METADATA_FIELD])
    term_catalog = json.loads(exported_rows[0][MARAWA_TERM_CATALOG_FIELD])

    assert reader.fieldnames == FULL_EXPORT_COLUMNS
    assert story_metadata == {
        "schema_version": 1,
        "completeness": "complete",
        "source_row_number": 1,
        "trope_assignments": [
            {"text": "first trope", "origin": "csv_import", "status": "validated"},
            {"text": "second trope", "origin": "semantic_suggestion", "status": "pending"},
        ],
    }
    assert {item["text"] for item in term_catalog["tropes"]} == {
        "first trope",
        "second trope",
        "unused canonical trope",
    }
    assert {item["text"] for item in term_catalog["themes"]} == {
        "Creation",
        "Ocean",
        "unused canonical theme",
    }
    assert {item["text"] for item in term_catalog["keywords"]} == {
        "wolf",
        "moon",
        "unused canonical keyword",
    }

    with make_session(tmp_path, "full-export-target.db") as session:
        imported_dataset = import_csv_bytes(session, exported, source_filename="full.csv")
        imported_story = session.scalar(select(Story).where(Story.dataset_id == imported_dataset.id))
        imported_tropes = {
            trope.text: (trope.confirmation_status, trope.review_status)
            for trope in session.scalars(select(Trope).where(Trope.dataset_id == imported_dataset.id)).all()
        }
        imported_themes = {
            theme.text: theme.confirmation_status
            for theme in session.scalars(select(Theme).where(Theme.dataset_id == imported_dataset.id)).all()
        }
        imported_keywords = {
            keyword.text: (keyword.confirmation_status, keyword.review_status)
            for keyword in session.scalars(select(Keyword).where(Keyword.dataset_id == imported_dataset.id)).all()
        }
        imported_trope_assignments = [
            (link.trope.text, link.origin, link.status)
            for link in sorted(imported_story.trope_links, key=lambda link: link.position)
        ]

    assert imported_story is not None
    assert imported_story.completeness == StoryCompleteness.COMPLETE
    assert imported_story.source_row_number == 1
    assert imported_trope_assignments == [
        ("first trope", StoryTropeOrigin.CSV_IMPORT, AssignmentStatus.VALIDATED),
        ("second trope", StoryTropeOrigin.SEMANTIC_SUGGESTION, AssignmentStatus.PENDING),
    ]
    assert imported_tropes == {
        "first trope": (TropeConfirmationStatus.CANONICAL, TermReviewStatus.REJECTED),
        "second trope": (TropeConfirmationStatus.UNCONFIRMED, TermReviewStatus.APPROVED),
        "unused canonical trope": (TropeConfirmationStatus.CANONICAL, TermReviewStatus.PENDING_REVIEW),
    }
    assert imported_themes == {
        "Creation": ThemeConfirmationStatus.CANONICAL,
        "Ocean": ThemeConfirmationStatus.UNCONFIRMED,
        "unused canonical theme": ThemeConfirmationStatus.CANONICAL,
    }
    assert imported_keywords == {
        "wolf": (KeywordConfirmationStatus.CANONICAL, TermReviewStatus.PENDING_REVIEW),
        "moon": (KeywordConfirmationStatus.UNCONFIRMED, TermReviewStatus.APPROVED),
        "unused canonical keyword": (KeywordConfirmationStatus.CANONICAL, TermReviewStatus.REJECTED),
    }


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
        dataset = import_csv_bytes(session, make_csv_bytes([first_row, second_row]), source_filename="input.csv")
        activate_dataset(session, dataset)
        exported_bytes = export_active_dataset_to_csv_bytes(session)

    reader = csv.DictReader(io.StringIO(exported_bytes.decode("utf-8-sig")))
    rows = list(reader)

    assert [row["Story title (Eng)"] for row in rows] == ["Story A", "Story B"]
    assert rows[0][KEYWORD_FIELD] == "pandanus ; woman"
    assert rows[0][TROPE_FIELD] == "§§ woman becomes tree"
    assert rows[1][KEYWORD_FIELD] == "canoe ; sea"
    assert rows[1][TROPE_FIELD] == "§§ younger sibling wins\n§§ eldest loses"
