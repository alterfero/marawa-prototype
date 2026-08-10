from __future__ import annotations

import csv
import hashlib
import io
import json
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload

from app.core.csv_schema import (
    CSV_COLUMNS,
    CSV_IMPORT_ALIASES,
    DATE_OF_RECORDING_FIELD,
    FULL_EXPORT_COLUMNS,
    KEYWORD_FIELD,
    MARAWA_STORY_METADATA_FIELD,
    MARAWA_TERM_CATALOG_FIELD,
    THEME_FIELD,
    TROPE_FIELD,
)
from app.core.dates import format_year_interval, normalize_imported_year_interval
from app.core.parsing import (
    clean_text,
    normalize_text,
    serialize_keywords,
    serialize_themes,
    serialize_tropes,
    split_keywords,
    split_themes,
    split_tropes,
)
from app.db.models import (
    AssignmentStatus,
    Dataset,
    DatasetStatus,
    Keyword,
    KeywordConfirmationStatus,
    Story,
    StoryCompleteness,
    StoryKeyword,
    StoryTheme,
    StoryTrope,
    StoryTropeOrigin,
    TermReviewStatus,
    Theme,
    ThemeConfirmationStatus,
    Trope,
    TropeConfirmationStatus,
)


class CSVImportValidationError(ValueError):
    """Raised when an uploaded CSV does not match the supported legacy contract."""


_CSV_FIELD_LIMIT_LOCK = Lock()
_MAX_CSV_FIELD_SIZE = 64 * 1024 * 1024


@dataclass(frozen=True)
class _CSVImportRow:
    source_row_number: int
    fields: dict[str, str]
    story_metadata: dict[str, Any] | None
    term_catalog: dict[str, Any] | None


def _normalize_header(fieldnames: list[str | None]) -> list[str]:
    return [clean_text(name) for name in fieldnames if name is not None]


@contextmanager
def _allow_large_csv_fields():
    """Temporarily allow a full-export metadata cell up to the upload-size ceiling.

    ``csv.field_size_limit`` is process-global, so the lock keeps concurrent
    uploads from changing the limit while another CSV is being parsed.
    """

    with _CSV_FIELD_LIMIT_LOCK:
        original_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(max(original_limit, _MAX_CSV_FIELD_SIZE))
            yield
        finally:
            csv.field_size_limit(original_limit)


def _resolve_import_columns(fieldnames: list[str]) -> list[str | None]:
    resolved_columns: list[str | None] = []
    seen_targets: dict[str, str] = {}
    duplicate_targets: list[str] = []

    for fieldname in fieldnames:
        target = fieldname if fieldname in FULL_EXPORT_COLUMNS else CSV_IMPORT_ALIASES.get(fieldname)
        resolved_columns.append(target)
        if target is None:
            continue
        previous_fieldname = seen_targets.get(target)
        if previous_fieldname is not None:
            duplicate_targets.append(target)
            continue
        seen_targets[target] = fieldname

    missing_columns = [column for column in CSV_COLUMNS if column not in seen_targets]
    if missing_columns:
        preview = ", ".join(missing_columns[:5])
        suffix = "..." if len(missing_columns) > 5 else ""
        raise CSVImportValidationError(f"The uploaded CSV is missing required legacy columns: {preview}{suffix}")

    if duplicate_targets:
        unique_duplicates = list(dict.fromkeys(duplicate_targets))
        preview = ", ".join(unique_duplicates[:5])
        suffix = "..." if len(unique_duplicates) > 5 else ""
        raise CSVImportValidationError(
            "The uploaded CSV maps multiple header columns to the same legacy field: "
            f"{preview}{suffix}."
        )

    return resolved_columns


def _parse_marawa_metadata(value: str, *, column: str, row_number: int) -> dict[str, Any] | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CSVImportValidationError(
            f"Data row {row_number} has invalid JSON in {column}."
        ) from exc
    if not isinstance(parsed, dict):
        raise CSVImportValidationError(
            f"Data row {row_number} must contain a JSON object in {column}."
        )
    return parsed


def _load_csv_rows(csv_bytes: bytes) -> tuple[list[_CSVImportRow], bool]:
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVImportValidationError(
            "The uploaded file could not be decoded as UTF-8 CSV. Please export it as UTF-8 and try again."
        ) from exc
    if "\x00" in text:
        raise CSVImportValidationError(
            "The uploaded file contains unexpected null bytes and could not be parsed as a valid CSV."
        )

    try:
        with _allow_large_csv_fields():
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            raw_fieldnames = next(reader, [])
            fieldnames = _normalize_header(raw_fieldnames)
            if not fieldnames:
                raise CSVImportValidationError("The uploaded file does not contain a readable CSV header row.")

            resolved_columns = _resolve_import_columns(fieldnames)
            has_story_metadata = MARAWA_STORY_METADATA_FIELD in fieldnames
            has_term_catalog = MARAWA_TERM_CATALOG_FIELD in fieldnames
            if has_story_metadata != has_term_catalog:
                raise CSVImportValidationError(
                    "A full Marawa export must include both Marawa story metadata and Marawa term catalog columns."
                )
            is_full_export = has_story_metadata and has_term_catalog

            rows: list[_CSVImportRow] = []
            for row_number, row_values in enumerate(reader, start=1):
                extra_values = row_values[len(fieldnames) :]
                if any(clean_text(value) for value in extra_values):
                    raise CSVImportValidationError(
                        f"Data row {row_number} has more values than the header defines. "
                        "Please check quoting and separators in the uploaded CSV."
                    )

                padded_values = list(row_values[: len(fieldnames)])
                if len(padded_values) < len(fieldnames):
                    padded_values.extend([""] * (len(fieldnames) - len(padded_values)))

                normalized_row = {column: "" for column in CSV_COLUMNS}
                extension_values: dict[str, str] = {}
                for column, value in zip(resolved_columns, padded_values, strict=True):
                    if column is None:
                        continue
                    if column in CSV_COLUMNS:
                        normalized_row[column] = clean_text(value)
                    else:
                        extension_values[column] = clean_text(value)
                if not any(normalized_row.values()) and not any(extension_values.values()):
                    continue
                rows.append(
                    _CSVImportRow(
                        source_row_number=row_number,
                        fields={column: normalized_row.get(column, "") for column in CSV_COLUMNS},
                        story_metadata=_parse_marawa_metadata(
                            extension_values.get(MARAWA_STORY_METADATA_FIELD, ""),
                            column=MARAWA_STORY_METADATA_FIELD,
                            row_number=row_number,
                        )
                        if is_full_export
                        else None,
                        term_catalog=_parse_marawa_metadata(
                            extension_values.get(MARAWA_TERM_CATALOG_FIELD, ""),
                            column=MARAWA_TERM_CATALOG_FIELD,
                            row_number=row_number,
                        )
                        if is_full_export
                        else None,
                    )
                )
    except csv.Error as exc:
        raise CSVImportValidationError(
            "The uploaded CSV is malformed. Please check quotes, separators, and line breaks, then try again. "
            f"Parser detail: {exc}"
        ) from exc

    if not rows:
        raise CSVImportValidationError("The uploaded CSV has a header row but no story entries.")

    return rows, is_full_export


def _row_hash(fields: dict[str, str]) -> str:
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_full_export_catalog(rows: list[_CSVImportRow]) -> dict[str, dict[str, dict[str, str]]]:
    if rows[0].term_catalog is None or any(row.term_catalog is not None for row in rows[1:]):
        raise CSVImportValidationError(
            "A full Marawa export must include its term catalog exactly once, on the first story row."
        )

    catalog = rows[0].term_catalog
    assert catalog is not None
    if catalog.get("schema_version") != 1:
        raise CSVImportValidationError("The Marawa term catalog has an unsupported schema version.")

    parsed_catalog: dict[str, dict[str, dict[str, str]]] = {
        "tropes": {},
        "themes": {},
        "keywords": {},
    }
    specifications = (
        ("tropes", TropeConfirmationStatus, True),
        ("themes", ThemeConfirmationStatus, False),
        ("keywords", KeywordConfirmationStatus, True),
    )
    for collection, confirmation_enum, includes_review_status in specifications:
        items = catalog.get(collection)
        if not isinstance(items, list):
            raise CSVImportValidationError(f"The Marawa term catalog must contain a {collection} array.")
        for item in items:
            if not isinstance(item, dict):
                raise CSVImportValidationError(f"Each {collection} entry in the Marawa term catalog must be an object.")
            text = clean_text(item.get("text", ""))
            marker = normalize_text(text)
            if not marker:
                raise CSVImportValidationError(f"Each {collection} entry in the Marawa term catalog needs text.")
            if marker in parsed_catalog[collection]:
                raise CSVImportValidationError(
                    f"The Marawa term catalog contains the {collection[:-1]} {text!r} more than once."
                )
            try:
                confirmation_status = confirmation_enum(clean_text(item.get("confirmation_status", ""))).value
            except ValueError as exc:
                raise CSVImportValidationError(
                    f"The {collection[:-1]} {text!r} has an invalid confirmation status in the Marawa term catalog."
                ) from exc

            parsed_item = {
                "text": text,
                "confirmation_status": confirmation_status,
            }
            if includes_review_status:
                try:
                    parsed_item["review_status"] = TermReviewStatus(clean_text(item.get("review_status", ""))).value
                except ValueError as exc:
                    raise CSVImportValidationError(
                        f"The {collection[:-1]} {text!r} has an invalid review status in the Marawa term catalog."
                    ) from exc
            parsed_catalog[collection][marker] = parsed_item

    return parsed_catalog


def _parse_full_story_metadata(
    row: _CSVImportRow,
    trope_texts: list[str],
) -> tuple[StoryCompleteness, int | None, list[tuple[StoryTropeOrigin, AssignmentStatus]]]:
    metadata = row.story_metadata
    if metadata is None:
        raise CSVImportValidationError(
            f"Data row {row.source_row_number} is missing Marawa story metadata."
        )
    if metadata.get("schema_version") != 1:
        raise CSVImportValidationError(
            f"Data row {row.source_row_number} has an unsupported Marawa story metadata schema version."
        )
    try:
        completeness = StoryCompleteness(clean_text(metadata.get("completeness", "")))
    except ValueError as exc:
        raise CSVImportValidationError(
            f"Data row {row.source_row_number} has an invalid story completeness value."
        ) from exc

    if "source_row_number" not in metadata:
        raise CSVImportValidationError(
            f"Data row {row.source_row_number} is missing its original source row number."
        )
    original_source_row_number = metadata["source_row_number"]
    if original_source_row_number is not None and (
        isinstance(original_source_row_number, bool)
        or not isinstance(original_source_row_number, int)
        or original_source_row_number < 1
    ):
        raise CSVImportValidationError(
            f"Data row {row.source_row_number} has an invalid original source row number."
        )

    assignments = metadata.get("trope_assignments")
    if not isinstance(assignments, list) or len(assignments) != len(trope_texts):
        raise CSVImportValidationError(
            f"Data row {row.source_row_number} has trope metadata that does not match Motifs (Eng)."
        )

    parsed_assignments: list[tuple[StoryTropeOrigin, AssignmentStatus]] = []
    for trope_text, assignment in zip(trope_texts, assignments, strict=True):
        if not isinstance(assignment, dict) or normalize_text(clean_text(assignment.get("text", ""))) != normalize_text(trope_text):
            raise CSVImportValidationError(
                f"Data row {row.source_row_number} has trope metadata in a different order from Motifs (Eng)."
            )
        try:
            origin = StoryTropeOrigin(clean_text(assignment.get("origin", "")))
            status = AssignmentStatus(clean_text(assignment.get("status", "")))
        except ValueError as exc:
            raise CSVImportValidationError(
                f"Data row {row.source_row_number} has an invalid trope assignment status or origin."
            ) from exc
        parsed_assignments.append((origin, status))

    return completeness, original_source_row_number, parsed_assignments


def _preload_full_export_catalog(
    session: Session,
    *,
    dataset_id: str,
    catalog: dict[str, dict[str, dict[str, str]]],
) -> tuple[dict[str, Trope], dict[str, Theme], dict[str, Keyword]]:
    tropes: dict[str, Trope] = {}
    themes: dict[str, Theme] = {}
    keywords: dict[str, Keyword] = {}

    for marker, item in catalog["tropes"].items():
        tropes[marker] = Trope(
            dataset_id=dataset_id,
            text=item["text"],
            confirmation_status=TropeConfirmationStatus(item["confirmation_status"]),
            review_status=TermReviewStatus(item["review_status"]),
        )
    for marker, item in catalog["themes"].items():
        themes[marker] = Theme(
            dataset_id=dataset_id,
            text=item["text"],
            confirmation_status=ThemeConfirmationStatus(item["confirmation_status"]),
        )
    for marker, item in catalog["keywords"].items():
        keywords[marker] = Keyword(
            dataset_id=dataset_id,
            text=item["text"],
            confirmation_status=KeywordConfirmationStatus(item["confirmation_status"]),
            review_status=TermReviewStatus(item["review_status"]),
        )

    session.add_all([*tropes.values(), *themes.values(), *keywords.values()])
    session.flush()
    return tropes, themes, keywords


def import_csv_bytes(
    session: Session,
    csv_bytes: bytes,
    *,
    source_filename: str | None = None,
) -> Dataset:
    rows, is_full_export = _load_csv_rows(csv_bytes)
    full_export_catalog = _parse_full_export_catalog(rows) if is_full_export else None

    dataset = Dataset(
        status=DatasetStatus.STAGED,
        source_filename=clean_text(source_filename) if source_filename is not None else None,
    )
    session.add(dataset)
    session.flush()

    trope_cache: dict[str, Trope] = {}
    keyword_cache: dict[str, Keyword] = {}
    theme_cache: dict[str, Theme] = {}
    trope_counts: dict[str, int] = {}
    keyword_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}

    existing_tropes = {
        trope.normalized_text: trope
        for trope in session.scalars(select(Trope).where(Trope.dataset_id == dataset.id)).all()
    }
    existing_keywords = {
        keyword.normalized_text: keyword
        for keyword in session.scalars(select(Keyword).where(Keyword.dataset_id == dataset.id)).all()
    }
    existing_themes = {
        theme.normalized_text: theme
        for theme in session.scalars(select(Theme).where(Theme.dataset_id == dataset.id)).all()
    }

    if full_export_catalog is not None:
        trope_cache, theme_cache, keyword_cache = _preload_full_export_catalog(
            session,
            dataset_id=dataset.id,
            catalog=full_export_catalog,
        )
        existing_tropes.update(trope_cache)
        existing_themes.update(theme_cache)
        existing_keywords.update(keyword_cache)

    used_source_row_numbers: set[int] = set()
    for row in rows:
        fields = dict(row.fields)
        recording_interval = normalize_imported_year_interval(fields.get(DATE_OF_RECORDING_FIELD, ""))
        fields[DATE_OF_RECORDING_FIELD] = (
            "" if recording_interval is None else format_year_interval(recording_interval.year1, recording_interval.year2)
        )
        tropes = split_tropes(fields.get(TROPE_FIELD, ""))
        keywords = split_keywords(fields.get(KEYWORD_FIELD, ""))
        themes = split_themes(fields.get(THEME_FIELD, ""))
        if is_full_export:
            completeness, source_row_number, trope_assignment_metadata = _parse_full_story_metadata(row, tropes)
        else:
            completeness = StoryCompleteness.INCOMPLETE
            source_row_number = row.source_row_number
            trope_assignment_metadata = [
                (StoryTropeOrigin.CSV_IMPORT, AssignmentStatus.VALIDATED) for _ in tropes
            ]
        if source_row_number is not None:
            if source_row_number in used_source_row_numbers:
                raise CSVImportValidationError(
                    f"Data row {row.source_row_number} duplicates an original source row number."
                )
            used_source_row_numbers.add(source_row_number)

        story = Story(
            dataset_id=dataset.id,
            source_row_number=source_row_number,
            fields_json=dict(fields),
            recording_year_start=None if recording_interval is None else recording_interval.year1,
            recording_year_end=None if recording_interval is None else recording_interval.year2,
            row_hash=_row_hash(fields),
            completeness=completeness,
        )
        session.add(story)
        session.flush()

        for position, trope_text in enumerate(tropes):
            marker = normalize_text(trope_text)
            trope = trope_cache.get(marker)
            if trope is None:
                if full_export_catalog is not None:
                    raise CSVImportValidationError(
                        f"Data row {row.source_row_number} assigns trope {trope_text!r}, which is missing from the Marawa term catalog."
                    )
                trope = existing_tropes.get(marker)
                if trope is None:
                    trope = Trope(dataset_id=dataset.id, text=trope_text)
                    session.add(trope)
                    session.flush()
                    existing_tropes[trope.normalized_text] = trope
                trope_cache[marker] = trope

            session.add(
                StoryTrope(
                    story_id=story.id,
                    trope_id=trope.id,
                    origin=trope_assignment_metadata[position][0],
                    status=trope_assignment_metadata[position][1],
                    position=position,
                )
            )
            trope_counts[trope.id] = trope_counts.get(trope.id, 0) + 1

        for position, keyword_text in enumerate(keywords):
            marker = normalize_text(keyword_text)
            keyword = keyword_cache.get(marker)
            if keyword is None:
                if full_export_catalog is not None:
                    raise CSVImportValidationError(
                        f"Data row {row.source_row_number} assigns keyword {keyword_text!r}, which is missing from the Marawa term catalog."
                    )
                keyword = existing_keywords.get(marker)
                if keyword is None:
                    keyword = Keyword(dataset_id=dataset.id, text=keyword_text)
                    session.add(keyword)
                    session.flush()
                    existing_keywords[keyword.normalized_text] = keyword
                keyword_cache[marker] = keyword

            session.add(
                StoryKeyword(
                    story_id=story.id,
                    keyword_id=keyword.id,
                    position=position,
                )
            )
            keyword_counts[keyword.id] = keyword_counts.get(keyword.id, 0) + 1

        for position, theme_text in enumerate(themes):
            marker = normalize_text(theme_text)
            theme = theme_cache.get(marker)
            if theme is None:
                if full_export_catalog is not None:
                    raise CSVImportValidationError(
                        f"Data row {row.source_row_number} assigns theme {theme_text!r}, which is missing from the Marawa term catalog."
                    )
                theme = existing_themes.get(marker)
                if theme is None:
                    theme = Theme(dataset_id=dataset.id, text=theme_text)
                    session.add(theme)
                    session.flush()
                    existing_themes[theme.normalized_text] = theme
                theme_cache[marker] = theme

            session.add(
                StoryTheme(
                    story_id=story.id,
                    theme_id=theme.id,
                    position=position,
                )
            )
            theme_counts[theme.id] = theme_counts.get(theme.id, 0) + 1

    for trope_id, count in trope_counts.items():
        trope = session.get(Trope, trope_id)
        if trope is not None:
            trope.cached_story_count = count

    for keyword_id, count in keyword_counts.items():
        keyword = session.get(Keyword, keyword_id)
        if keyword is not None:
            keyword.cached_story_count = count

    for theme_id, count in theme_counts.items():
        theme = session.get(Theme, theme_id)
        if theme is not None:
            theme.cached_story_count = count

    session.commit()
    session.refresh(dataset)
    return dataset


def _ordered_trope_links(story: Story) -> list[StoryTrope]:
    return sorted(
        (link for link in story.trope_links if link.trope is not None),
        key=lambda item: (
            item.position is None,
            item.position if item.position is not None else 0,
            item.created_at,
            item.trope.text,
        ),
    )


def _ordered_keyword_links(story: Story) -> list[StoryKeyword]:
    return sorted(
        (link for link in story.keyword_links if link.keyword is not None),
        key=lambda item: (
            item.position is None,
            item.position if item.position is not None else 0,
            item.created_at,
            item.keyword.text,
        ),
    )


def _ordered_theme_links(story: Story) -> list[StoryTheme]:
    return sorted(
        (link for link in story.theme_links if link.theme is not None),
        key=lambda item: (
            item.position is None,
            item.position if item.position is not None else 0,
            item.created_at,
            item.theme.text,
        ),
    )


def _serialize_metadata(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _full_term_catalog(session: Session, dataset_id: str) -> dict[str, Any]:
    tropes = session.scalars(
        select(Trope).where(Trope.dataset_id == dataset_id).order_by(Trope.normalized_text, Trope.id)
    ).all()
    themes = session.scalars(
        select(Theme).where(Theme.dataset_id == dataset_id).order_by(Theme.normalized_text, Theme.id)
    ).all()
    keywords = session.scalars(
        select(Keyword).where(Keyword.dataset_id == dataset_id).order_by(Keyword.normalized_text, Keyword.id)
    ).all()
    return {
        "schema_version": 1,
        "tropes": [
            {
                "text": trope.text,
                "confirmation_status": trope.confirmation_status.value,
                "review_status": trope.review_status.value,
            }
            for trope in tropes
        ],
        "themes": [
            {
                "text": theme.text,
                "confirmation_status": theme.confirmation_status.value,
            }
            for theme in themes
        ],
        "keywords": [
            {
                "text": keyword.text,
                "confirmation_status": keyword.confirmation_status.value,
                "review_status": keyword.review_status.value,
            }
            for keyword in keywords
        ],
    }


def _full_story_metadata(story: Story, trope_links: list[StoryTrope]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "completeness": story.completeness.value,
        "source_row_number": story.source_row_number,
        "trope_assignments": [
            {
                "text": link.trope.text,
                "origin": link.origin.value,
                "status": link.status.value,
            }
            for link in trope_links
        ],
    }


def export_dataset_to_csv_bytes(
    session: Session,
    *,
    dataset_id: str,
    include_marawa_metadata: bool = False,
) -> bytes:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise CSVImportValidationError("The requested dataset is no longer available to export.")

    stories = session.scalars(
        select(Story)
        .where(Story.dataset_id == dataset.id)
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
            selectinload(Story.theme_links).selectinload(StoryTheme.theme),
        )
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()

    buffer = io.StringIO(newline="")
    fieldnames = FULL_EXPORT_COLUMNS if include_marawa_metadata else CSV_COLUMNS
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    serialized_term_catalog = _serialize_metadata(_full_term_catalog(session, dataset.id)) if include_marawa_metadata else ""

    for story_index, story in enumerate(stories):
        row = {column: clean_text(story.fields_json.get(column, "")) for column in CSV_COLUMNS}
        if story.recording_year_start is not None and story.recording_year_end is not None:
            row[DATE_OF_RECORDING_FIELD] = format_year_interval(story.recording_year_start, story.recording_year_end)
        else:
            row[DATE_OF_RECORDING_FIELD] = ""
        trope_links = _ordered_trope_links(story)
        keyword_links = _ordered_keyword_links(story)
        theme_links = _ordered_theme_links(story)
        trope_texts = [link.trope.text for link in trope_links]
        keyword_texts = [link.keyword.text for link in keyword_links]
        theme_texts = [link.theme.text for link in theme_links]
        row[TROPE_FIELD] = serialize_tropes(trope_texts)
        row[KEYWORD_FIELD] = serialize_keywords(keyword_texts)
        row[THEME_FIELD] = serialize_themes(theme_texts)
        if include_marawa_metadata:
            row[MARAWA_STORY_METADATA_FIELD] = _serialize_metadata(_full_story_metadata(story, trope_links))
            row[MARAWA_TERM_CATALOG_FIELD] = serialized_term_catalog if story_index == 0 else ""
        writer.writerow(row)

    return buffer.getvalue().encode("utf-8-sig")


def export_active_dataset_to_csv_bytes(session: Session, *, include_marawa_metadata: bool = False) -> bytes:
    dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if dataset is None:
        raise CSVImportValidationError("No active dataset is available to export.")
    return export_dataset_to_csv_bytes(
        session,
        dataset_id=dataset.id,
        include_marawa_metadata=include_marawa_metadata,
    )
