import csv
import io
import json

import pytest
from sqlalchemy import func, select

from app.core.csv_schema import CSV_COLUMNS, DATE_OF_RECORDING_FIELD, FULL_EXPORT_COLUMNS, MARAWA_STORY_METADATA_FIELD, MARAWA_TERM_CATALOG_FIELD, KEYWORD_FIELD, THEME_FIELD, TROPE_FIELD
from app.core.injection import duplicate_keys, inspect_csv
from app.db.models import CSVInjection, Dataset, Job, Story, Trope, Theme, Keyword, TermReviewStatus
from app.services.csv_io import export_active_dataset_to_csv_bytes, import_csv_bytes
from app.services.injection import InjectionError, apply_injection, get_injection, preview_injection
from tests.test_csv_io import activate_dataset, make_csv_bytes, make_session


def setup_dataset(session, rows=None):
    dataset = import_csv_bytes(session, make_csv_bytes(rows or [{"Story title (Eng)": "Original", "Abstract (Eng)": "Old text"}]))
    activate_dataset(session, dataset)
    return dataset


def preview(session, rows, **kwargs):
    return preview_injection(session, make_csv_bytes(rows, **kwargs), source_filename="incoming.csv")


def apply(session, report, decisions=None, acknowledge=True):
    return apply_injection(session, report["id"], decisions=decisions or [], acknowledge_warnings=acknowledge)


def test_preview_checks_every_row_and_reports_physical_lines(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        report = preview(session, [
            {"Story title (Eng)": "A\nmultiline title", "date of recording": "[2000, 1900]", "space coord": "invalid"},
            {"Story title (Eng)": "B", "date of recording": "unknown", "URL ?": "not a URL"},
        ])
        assert report["row_count"] == 2
        assert report["error_count"] == 0
        assert report["warning_count"] == 2
        assert {i["row_number"] for i in report["issues"]} == {1, 2}
        assert next(i for i in report["issues"] if i["row_number"] == 2)["line_start"] == 4
        assert all(i["suggestion"] for i in report["issues"])
        assert all(row["fields"][DATE_OF_RECORDING_FIELD] == "" for row in report["rows"])
        apply(session, report)
        assert session.scalar(select(func.count(Story.id))) == 3


def test_header_and_short_and_extra_rows_are_actionable(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        report = preview(session, [{"Story title (Eng)": "X"}], fieldnames=["Story title (Eng)"])
        assert report["error_count"] == len(CSV_COLUMNS) - 1
        content = make_csv_bytes([{"Story title (Eng)": "Good"}]) + b"short,row\n" + ("," * len(CSV_COLUMNS) + "overflow\n").encode()
        report = preview_injection(session, content, source_filename="broken.csv")
        assert {i["code"] for i in report["issues"]} >= {"row_too_short", "row_too_long"}
        assert report["row_count"] == 3


@pytest.mark.parametrize("action,count,title", [("keep_both", 2, "Original"), ("keep_original", 1, "Original"), ("keep_injected", 1, "Original")])
def test_duplicate_choices_preserve_original_identity(tmp_path, action, count, title):
    with make_session(tmp_path) as session:
        dataset = setup_dataset(session)
        original = session.scalar(select(Story))
        original_id = original.id
        report = preview(session, [{"Story title (Eng)": "Original", "Abstract (Eng)": "old   TEXT", TROPE_FIELD: "new trope"}])
        assert report["duplicate_count"] == 1
        with pytest.raises(InjectionError):
            apply(session, report)
        result = apply(session, report, [{"row_number": 1, "action": action, "target_key": f"story:{original_id}"}])
        session.expire_all()
        assert session.scalar(select(func.count(Story.id))) == count
        original = session.get(Story, original_id)
        assert original.fields_json["Story title (Eng)"] == title
        assert original.fields_json["Abstract (Eng)"] == ("old   TEXT" if action == "keep_injected" else "Old text")
        assert session.get(Dataset, dataset.id).id == dataset.id
        assert result["status"] == "applied"
        assert apply(session, report) == result  # A retry must never inject twice.


def test_duplicates_inside_file_are_resolved_in_row_order(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        report = preview(session, [{"Story title (Eng)": "New", "Source": "book", "Abstract (Eng)": "Later"}, {"Story title (Eng)": " new ", "Source": "new book", "Abstract (Eng)": "Later"}])
        assert report["rows"][1]["candidates"][0]["key"] == "row:1"
        apply(session, report, [{"row_number": 2, "action": "keep_injected", "target_key": "row:1"}])
        assert session.scalar(select(func.count(Story.id))) == 2
        assert session.scalar(select(Story).where(Story.fields_json["Abstract (Eng)"].as_string() == "Later"))


def test_stale_preview_and_warning_acknowledgement(tmp_path):
    with make_session(tmp_path) as session:
        dataset = setup_dataset(session)
        report = preview(session, [{"Story title (Eng)": "New", "space coord": "unknown"}])
        with pytest.raises(InjectionError, match="warnings"):
            apply(session, report, acknowledge=False)
        dataset.version += 1
        session.commit()
        with pytest.raises(InjectionError, match="changed"):
            apply(session, report)
        assert session.scalar(select(func.count(Story.id))) == 1


def test_full_export_injection_preserves_metadata_and_unused_terms(tmp_path):
    with make_session(tmp_path) as source:
        setup_dataset(source, [{"Story title (Eng)": "Imported", TROPE_FIELD: "shared"}])
        source.add(Trope(dataset_id=source.scalar(select(Dataset.id)), text="unused", confirmation_status="canonical"))
        source.commit()
        content = export_active_dataset_to_csv_bytes(source, include_marawa_metadata=True)
    with make_session(tmp_path, "destination.db") as session:
        setup_dataset(session)
        report = preview_injection(session, content, source_filename="full.csv")
        assert report["error_count"] == 0
        apply(session, report)
        exported = list(csv.DictReader(io.StringIO(export_active_dataset_to_csv_bytes(session, include_marawa_metadata=True).decode("utf-8-sig"))))
        assert len(exported) == 2
        assert json.loads(exported[1][MARAWA_STORY_METADATA_FIELD])["trope_assignments"][0]["origin"] == "csv_import"
        catalog = json.loads(exported[0][MARAWA_TERM_CATALOG_FIELD])
        assert {t["text"] for t in catalog["tropes"]} == {"unused", "shared"}
        assert session.scalar(select(func.count(Job.id))) == 1


def test_full_export_metadata_errors_are_collected_for_each_row(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        report = preview(session, [
            {"Story title (Eng)": "A", MARAWA_STORY_METADATA_FIELD: "{broken", MARAWA_TERM_CATALOG_FIELD: "{}"},
            {"Story title (Eng)": "B", MARAWA_STORY_METADATA_FIELD: '{"schema_version":99}'},
        ], fieldnames=FULL_EXPORT_COLUMNS)
        assert report["error_count"] >= 3
        assert {i["row_number"] for i in report["issues"]} >= {1, 2}


def test_rollback_on_late_failure_leaves_no_partial_injection(tmp_path, monkeypatch):
    with make_session(tmp_path) as session:
        dataset = setup_dataset(session)
        report = preview(session, [{"Story title (Eng)": "One", TROPE_FIELD: "new"}, {"Story title (Eng)": "Two"}])
        version = dataset.version
        def fail(*args, **kwargs):
            raise RuntimeError("queue unavailable")
        monkeypatch.setattr("app.services.injection.queue_job", fail)
        with pytest.raises(RuntimeError):
            apply(session, report)
        assert session.scalar(select(func.count(Story.id))) == 1
        assert session.scalar(select(func.count(Trope.id))) == 0
        assert session.get(Dataset, dataset.id).version == version


def test_noop_keep_original_keeps_dataset_version_and_counts(tmp_path):
    with make_session(tmp_path) as session:
        dataset = setup_dataset(session)
        report = preview(session, [{"Story title (Eng)": "Original", "Abstract (Eng)": "Old text", TROPE_FIELD: "ignored"}])
        result = apply(session, report, [{"row_number": 1, "action": "keep_original", "target_key": report["rows"][0]["candidates"][0]["key"]}])
        assert result["dataset_version"] == dataset.version == 1
        assert result["job_id"] is None
        assert session.scalar(select(func.count(Trope.id))) == 0


def test_replacement_keeps_other_matches_and_recounts_assignments(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session, [{"Story title (Eng)": "Same", "Abstract (Eng)": "Shared account", TROPE_FIELD: "old"}, {"Story title (Eng)": "Same", "Abstract (Eng)": "Shared account", TROPE_FIELD: "old"}])
        report = preview(session, [{"Story title (Eng)": "Same", "Abstract (Eng)": "Shared account", TROPE_FIELD: "new"}])
        assert len(report["rows"][0]["candidates"]) == 2
        apply(session, report, [{"row_number": 1, "action": "keep_injected", "target_key": report["rows"][0]["candidates"][0]["key"]}])
        assert session.scalar(select(func.count(Story.id))) == 2
        assert {t.text: t.cached_story_count for t in session.scalars(select(Trope))} == {"old": 1, "new": 1}


def test_intrafile_chain_of_replacements_with_trope_metadata(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        report = preview(session, [{"Story title (Eng)": "Chain", "Abstract (Eng)": "Shared account", TROPE_FIELD: "first"}, {"Story title (Eng)": "Chain", "Abstract (Eng)": "Shared account", TROPE_FIELD: "second"}, {"Story title (Eng)": "Chain", "Abstract (Eng)": "Shared account", TROPE_FIELD: "third"}])
        result = apply(session, report, [{"row_number": 2, "action": "keep_injected", "target_key": "row:1"}, {"row_number": 3, "action": "keep_injected", "target_key": "row:2"}])
        assert result["added"] == 1 and result["updated"] == 2
        assert session.scalar(select(func.count(Story.id))) == 2
        assert session.scalar(select(Trope).where(Trope.text == "third")).cached_story_count == 1


@pytest.mark.parametrize("value,expected", [
    ("1998", "[1998, 1998]"),
    (" 1998 ", "[1998, 1998]"),
    ("[1971, 1980]", "[1971, 1980]"),
    (" [1971,1980] ", "[1971, 1980]"),
    ("[1998, 1998]", "[1998, 1998]"),
    ("1800", "[1800, 1800]"),
    ("2050", "[2050, 2050]"),
    ("", ""), ("unknown", ""), ("4 March 1998", ""),
    ("2024-02-29", ""), ("circa 1998", ""), ("1998-2000", ""),
    ("[2000, 1900]", ""), ("[1998, 2000", ""), ("1998, 2000", ""),
    ("[1799, 1801]", ""), ("2051", ""), ("1998.0", ""),
])
def test_injection_only_accepts_year_or_interval_without_date_messages(value, expected):
    parsed = inspect_csv(make_csv_bytes([{ "Story title (Eng)": "A story", DATE_OF_RECORDING_FIELD: value}]))
    assert parsed.rows[0]["fields"][DATE_OF_RECORDING_FIELD] == expected
    assert parsed.issues == []


def full_injection_rows(rows):
    result = []
    for index, fields in enumerate(rows):
        row = dict(fields)
        row[MARAWA_STORY_METADATA_FIELD] = json.dumps({
            "schema_version": 1, "completeness": "incomplete",
            "source_row_number": 1, "trope_assignments": [],
        })
        if index == 0:
            row[MARAWA_TERM_CATALOG_FIELD] = json.dumps({
                "schema_version": 1, "tropes": [], "themes": [], "keywords": [],
            })
        result.append(row)
    return result


@pytest.mark.parametrize("full_export", [False, True])
def test_injection_date_cleanup_reaches_database_and_export(tmp_path, full_export):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        rows = [
            {"Story title (Eng)": "Year", DATE_OF_RECORDING_FIELD: "1998"},
            {"Story title (Eng)": "Interval", DATE_OF_RECORDING_FIELD: "[1971, 1980]"},
            {"Story title (Eng)": "Free text", DATE_OF_RECORDING_FIELD: "4 March 1998"},
            {"Story title (Eng)": "Reversed", DATE_OF_RECORDING_FIELD: "[2000, 1900]"},
        ]
        report = preview(session, full_injection_rows(rows) if full_export else rows,
                         fieldnames=FULL_EXPORT_COLUMNS if full_export else CSV_COLUMNS)
        assert report["error_count"] == report["warning_count"] == 0
        apply(session, report, acknowledge=False)
        stories = {s.fields_json["Story title (Eng)"]: s for s in session.scalars(select(Story))}
        assert (stories["Year"].recording_year_start, stories["Year"].recording_year_end) == (1998, 1998)
        assert (stories["Interval"].recording_year_start, stories["Interval"].recording_year_end) == (1971, 1980)
        for title in ("Free text", "Reversed"):
            assert stories[title].recording_year_start is None
            assert stories[title].recording_year_end is None
            assert stories[title].fields_json[DATE_OF_RECORDING_FIELD] == ""
        exported = list(csv.DictReader(io.StringIO(export_active_dataset_to_csv_bytes(session).decode("utf-8-sig"))))
        assert {r["Story title (Eng)"]: r[DATE_OF_RECORDING_FIELD] for r in exported} == {
            "Original": "", "Year": "[1998, 1998]", "Interval": "[1971, 1980]", "Free text": "", "Reversed": "",
        }


def test_full_injection_ignores_all_incoming_source_row_numbers(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        original = session.scalar(select(Story))
        original.source_row_number = 531
        session.commit()
        rows = full_injection_rows([{"Story title (Eng)": f"New {i}"} for i in range(7)])
        for row, source_number in zip(rows, [531, 99999, None, -1, "bad", "missing", 531]):
            metadata = json.loads(row[MARAWA_STORY_METADATA_FIELD])
            if source_number == "missing":
                del metadata["source_row_number"]
            else:
                metadata["source_row_number"] = source_number
            row[MARAWA_STORY_METADATA_FIELD] = json.dumps(metadata)
        report = preview(session, rows, fieldnames=FULL_EXPORT_COLUMNS)
        assert report["error_count"] == report["warning_count"] == report["duplicate_count"] == 0
        apply(session, report, acknowledge=False)
        assert sorted(session.scalars(select(Story.source_row_number))) == list(range(531, 539))


def test_source_rows_are_allocated_only_for_new_stories(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        original = session.scalar(select(Story))
        rows = full_injection_rows([
            {"Story title (Eng)": "Original", "Abstract (Eng)": "Old text"}, {"Story title (Eng)": "Original", "Abstract (Eng)": "Old text", "Source": "Replacement"},
            {"Story title (Eng)": "New"}, {"Story title (Eng)": "Original", "Abstract (Eng)": "Old text", "Source": "Another version"},
        ])
        report = preview(session, rows, fieldnames=FULL_EXPORT_COLUMNS)
        assert report["warning_count"] == 0
        apply(session, report, [
            {"row_number": 1, "action": "keep_original", "target_key": f"story:{original.id}"},
            {"row_number": 2, "action": "keep_injected", "target_key": f"story:{original.id}"},
            {"row_number": 4, "action": "keep_both"},
        ], acknowledge=False)
        assert session.get(Story, original.id).source_row_number == 1
        assert sorted(session.scalars(select(Story.source_row_number))) == [1, 2, 3]


def test_old_pending_reviews_require_revalidation_under_new_rules(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        report = preview(session, [{"Story title (Eng)": "New"}])
        draft = session.get(CSVInjection, report["id"])
        draft.plan_json = {k: v for k, v in draft.plan_json.items() if k != "validation_version"}
        session.commit()
        with pytest.raises(InjectionError, match="rules have changed"):
            apply(session, report)
        assert session.scalar(select(func.count(Story.id))) == 1



def test_shared_term_statuses_are_kept_without_warnings(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session, [{
            "Story title (Eng)": "Original", TROPE_FIELD: "shared trope",
            THEME_FIELD: "shared theme", KEYWORD_FIELD: "shared keyword",
        }])
        terms = [session.scalar(select(model)) for model in (Trope, Theme, Keyword)]
        for term in terms:
            term.confirmation_status = type(term.confirmation_status)("canonical")
            if hasattr(term, "review_status"):
                term.review_status = TermReviewStatus.APPROVED
        session.commit()
        rows = full_injection_rows([{
            "Story title (Eng)": "Injected", TROPE_FIELD: "shared trope",
            THEME_FIELD: "shared theme", KEYWORD_FIELD: "shared keyword",
        }])
        metadata = json.loads(rows[0][MARAWA_STORY_METADATA_FIELD])
        metadata["trope_assignments"] = [{"text": "shared trope", "origin": "csv_import", "status": "validated"}]
        rows[0][MARAWA_STORY_METADATA_FIELD] = json.dumps(metadata)
        rows[0][MARAWA_TERM_CATALOG_FIELD] = json.dumps({
            "schema_version": 1,
            "tropes": [{"text": "shared trope", "confirmation_status": "unconfirmed", "review_status": "rejected"}],
            "themes": [{"text": "shared theme", "confirmation_status": "unconfirmed"}],
            "keywords": [{"text": "shared keyword", "confirmation_status": "unconfirmed", "review_status": "rejected"}],
        })
        report = preview(session, rows, fieldnames=FULL_EXPORT_COLUMNS)
        assert report["issues"] == []
        assert report["warning_count"] == 0
        result = apply(session, report, acknowledge=False)
        assert result["new_terms"] == 0
        session.expire_all()
        for term in terms:
            assert term.confirmation_status.value == "canonical"
            assert term.cached_story_count == 2
            if hasattr(term, "review_status"):
                assert term.review_status == TermReviewStatus.APPROVED


@pytest.mark.parametrize("other_warning", [False, True])
def test_saved_reviews_ignore_obsolete_term_status_warnings(tmp_path, other_warning):
    with make_session(tmp_path) as session:
        setup_dataset(session)
        fields = {"Story title (Eng)": "New"}
        if other_warning:
            fields["space coord"] = "unknown"
        report = preview(session, [fields])
        draft = session.get(CSVInjection, report["id"])
        draft.report_json = {
            **draft.report_json,
            "issues": [*report["issues"], {
                "severity": "warning", "code": "existing_term_status",
                "message": "Existing trope has different statuses.",
            }],
            "warning_count": report["warning_count"] + 1,
        }
        session.commit()
        restored = get_injection(session, report["id"])
        assert restored["issues"] == report["issues"]
        assert restored["warning_count"] == int(other_warning)
        if other_warning:
            with pytest.raises(InjectionError, match="warnings"):
                apply(session, report, acknowledge=False)
        result = apply(session, report, acknowledge=other_warning)
        assert result["added"] == 1


@pytest.mark.parametrize("incoming,expected", [
    ({"Story title (Eng)": "Legend", "Abstract (Eng)": "Short abstract"}, True),
    ({"Story title (Eng)": " LEGEND ", "Abstract (Eng)": " short   ABSTRACT "}, True),
    ({"Story title (Eng)": "Legend", "Abstract (Eng)": "Different abstract"}, False),
    ({"Story title (Eng)": "Different title", "Abstract (Eng)": "Short abstract"}, False),
    ({"Story title (Eng)": "Legend"}, False),
    ({"Abstract (Eng)": "Short abstract"}, False),
    ({"Story title (French)": "Legend", "Abstract (Fr)": "Short abstract"}, True),
])
def test_duplicate_requires_a_shared_nonempty_title_and_abstract(incoming, expected):
    existing = {"Story title (Eng)": "Legend", "Abstract (Eng)": "Short abstract"}
    assert bool(set(duplicate_keys(existing)) & set(duplicate_keys(incoming))) == expected
    assert duplicate_keys({"Story title (Eng)": "Legend"}) == []
    assert duplicate_keys({"Abstract (Eng)": "Short abstract"}) == []


def test_title_and_abstract_must_match_the_same_story(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session, [
            {"Story title (Eng)": "Legend", "Abstract (Eng)": "First account"},
            {"Story title (Eng)": "Other", "Abstract (Eng)": "Second account"},
        ])
        report = preview(session, [{"Story title (Eng)": "Legend", "Abstract (Eng)": "Second account"}])
        assert report["duplicate_count"] == report["warning_count"] == 0
        assert report["rows"][0]["fields"]["Story title (Eng)"] == "Legend 1"
        apply(session, report, acknowledge=False)
        assert session.scalar(select(func.count(Story.id))) == 3


@pytest.mark.parametrize("full_export", [False, True])
def test_title_only_matches_are_numbered_without_warnings_and_exported(tmp_path, full_export):
    with make_session(tmp_path) as session:
        setup_dataset(session, [
            {"Story title (Eng)": "The legend of Titua", "Abstract (Eng)": "Original account"},
            {"Story title (Eng)": "The legend of Titua 1", "Abstract (Eng)": "Another account"},
        ])
        rows = [
            {"Story title (Eng)": "The legend of Titua", "Abstract (Eng)": "First incoming account"},
            {"Story title (Eng)": "The legend of Titua", "Abstract (Eng)": "Second incoming account"},
            {"Story title (Eng)": "The legend of Titua 2", "Abstract (Eng)": "Explicitly numbered account"},
        ]
        report = preview(session, full_injection_rows(rows) if full_export else rows,
                         fieldnames=FULL_EXPORT_COLUMNS if full_export else CSV_COLUMNS)
        assert report["error_count"] == report["warning_count"] == report["duplicate_count"] == 0
        assert [r["fields"]["Story title (Eng)"] for r in report["rows"]] == [
            "The legend of Titua 3", "The legend of Titua 4", "The legend of Titua 2",
        ]
        assert get_injection(session, report["id"])["rows"] == report["rows"]
        apply(session, report, acknowledge=False)
        exported = list(csv.DictReader(io.StringIO(export_active_dataset_to_csv_bytes(session).decode("utf-8-sig"))))
        assert [r["Story title (Eng)"] for r in exported] == [
            "The legend of Titua", "The legend of Titua 1", "The legend of Titua 3", "The legend of Titua 4", "The legend of Titua 2",
        ]


@pytest.mark.parametrize("action,expected_count", [("keep_both", 3), ("keep_original", 2), ("keep_injected", 2)])
def test_repeat_of_automatically_numbered_entry_is_still_a_duplicate(tmp_path, action, expected_count):
    with make_session(tmp_path) as session:
        setup_dataset(session, [{"Story title (Eng)": "Legend", "Abstract (Eng)": "Original"}])
        report = preview(session, [
            {"Story title (Eng)": "Legend", "Abstract (Eng)": "New", "Source": "First"},
            {"Story title (Eng)": "Legend", "Abstract (Eng)": "New", "Source": "Second"},
        ])
        assert report["duplicate_count"] == 1
        assert report["warning_count"] == 0
        assert report["rows"][1]["candidates"][0]["key"] == "row:1"
        assert [r["fields"]["Story title (Eng)"] for r in report["rows"]] == ["Legend 1", "Legend 1"]
        apply(session, report, [{"row_number": 2, "action": action, "target_key": "row:1"}], acknowledge=False)
        assert session.scalar(select(func.count(Story.id))) == expected_count
        new_stories = session.scalars(select(Story).where(Story.fields_json["Abstract (Eng)"].as_string() == "New")).all()
        assert {s.fields_json["Story title (Eng)"] for s in new_stories} == {"Legend 1"}
        if action == "keep_injected":
            assert new_stories[0].fields_json["Source"] == "Second"


def test_numbering_applies_to_colliding_translated_titles_only(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session, [{"Story title (French)": "La légende", "Abstract (Fr)": "Ancien récit"}])
        report = preview(session, [{
            "Story title (Eng)": "An English title", "Story title (French)": "La légende",
            "Story title (other)": "La légende", "Abstract (Fr)": "Nouveau récit",
        }])
        assert report["duplicate_count"] == report["warning_count"] == 0
        assert report["rows"][0]["fields"]["Story title (Eng)"] == "An English title"
        assert report["rows"][0]["fields"]["Story title (French)"] == "La légende 1"
        assert report["rows"][0]["fields"]["Story title (other)"] == "La légende 1"


def test_missing_abstract_does_not_make_a_title_match_a_duplicate(tmp_path):
    with make_session(tmp_path) as session:
        setup_dataset(session, [{"Story title (Eng)": "Legend"}])
        report = preview(session, [{"Story title (Eng)": "Legend"}, {"Story title (Eng)": "Legend"}])
        assert report["duplicate_count"] == report["warning_count"] == 0
        assert [r["fields"]["Story title (Eng)"] for r in report["rows"]] == ["Legend 1", "Legend 2"]
