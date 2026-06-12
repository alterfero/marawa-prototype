from __future__ import annotations

import csv
import hashlib
import io
import json

from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload

from app.core.csv_schema import CSV_COLUMNS, CSV_IMPORT_ALIASES, KEYWORD_FIELD, TROPE_FIELD
from app.core.parsing import clean_text, normalize_text, serialize_keywords, serialize_tropes, split_keywords, split_tropes
from app.db.models import (
    AssignmentStatus,
    Dataset,
    DatasetStatus,
    Keyword,
    Story,
    StoryKeyword,
    StoryTrope,
    StoryTropeOrigin,
    Trope,
)


class CSVImportValidationError(ValueError):
    """Raised when an uploaded CSV does not match the supported legacy contract."""


def _normalize_header(fieldnames: list[str | None]) -> list[str]:
    return [clean_text(name) for name in fieldnames if name is not None]


def _resolve_import_columns(fieldnames: list[str]) -> list[str | None]:
    resolved_columns: list[str | None] = []
    seen_targets: dict[str, str] = {}
    duplicate_targets: list[str] = []

    for fieldname in fieldnames:
        target = fieldname if fieldname in CSV_COLUMNS else CSV_IMPORT_ALIASES.get(fieldname)
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


def _load_csv_rows(csv_bytes: bytes) -> list[tuple[int, dict[str, str]]]:
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
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        raw_fieldnames = next(reader, [])
        fieldnames = _normalize_header(raw_fieldnames)
        if not fieldnames:
            raise CSVImportValidationError("The uploaded file does not contain a readable CSV header row.")

        resolved_columns = _resolve_import_columns(fieldnames)

        rows: list[tuple[int, dict[str, str]]] = []
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
            for column, value in zip(resolved_columns, padded_values, strict=True):
                if column is None:
                    continue
                normalized_row[column] = clean_text(value)
            if not any(normalized_row.values()):
                continue
            rows.append((row_number, {column: normalized_row.get(column, "") for column in CSV_COLUMNS}))
    except csv.Error as exc:
        raise CSVImportValidationError(
            "The uploaded CSV is malformed. Please check quotes, separators, and line breaks, then try again."
        ) from exc

    if not rows:
        raise CSVImportValidationError("The uploaded CSV has a header row but no story entries.")

    return rows


def _row_hash(fields: dict[str, str]) -> str:
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _archive_active_dataset(session: Session) -> None:
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        return
    active_dataset.status = DatasetStatus.ARCHIVED
    session.flush()


def import_csv_bytes(
    session: Session,
    csv_bytes: bytes,
    *,
    source_filename: str | None = None,
) -> Dataset:
    rows = _load_csv_rows(csv_bytes)

    _archive_active_dataset(session)

    dataset = Dataset(
        status=DatasetStatus.ACTIVE,
        source_filename=clean_text(source_filename) if source_filename is not None else None,
    )
    session.add(dataset)
    session.flush()

    trope_cache: dict[str, Trope] = {}
    keyword_cache: dict[str, Keyword] = {}
    trope_counts: dict[str, int] = {}
    keyword_counts: dict[str, int] = {}

    existing_tropes = {
        trope.normalized_text: trope
        for trope in session.scalars(select(Trope)).all()
    }
    existing_keywords = {
        keyword.normalized_text: keyword
        for keyword in session.scalars(select(Keyword)).all()
    }

    for row_number, fields in rows:
        story = Story(
            dataset_id=dataset.id,
            source_row_number=row_number,
            fields_json=dict(fields),
            row_hash=_row_hash(fields),
        )
        session.add(story)
        session.flush()

        tropes = split_tropes(fields.get(TROPE_FIELD, ""))
        keywords = split_keywords(fields.get(KEYWORD_FIELD, ""))

        for position, trope_text in enumerate(tropes):
            marker = normalize_text(trope_text)
            trope = trope_cache.get(marker)
            if trope is None:
                trope = existing_tropes.get(marker)
                if trope is None:
                    trope = Trope(text=trope_text)
                    session.add(trope)
                    session.flush()
                    existing_tropes[trope.normalized_text] = trope
                trope_cache[marker] = trope

            session.add(
                StoryTrope(
                    story_id=story.id,
                    trope_id=trope.id,
                    origin=StoryTropeOrigin.CSV_IMPORT,
                    status=AssignmentStatus.VALIDATED,
                    position=position,
                )
            )
            trope_counts[trope.id] = trope_counts.get(trope.id, 0) + 1

        for position, keyword_text in enumerate(keywords):
            marker = normalize_text(keyword_text)
            keyword = keyword_cache.get(marker)
            if keyword is None:
                keyword = existing_keywords.get(marker)
                if keyword is None:
                    keyword = Keyword(text=keyword_text)
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

    for trope_id, count in trope_counts.items():
        trope = session.get(Trope, trope_id)
        if trope is not None:
            trope.cached_story_count = count

    for keyword_id, count in keyword_counts.items():
        keyword = session.get(Keyword, keyword_id)
        if keyword is not None:
            keyword.cached_story_count = count

    session.commit()
    session.refresh(dataset)
    return dataset


def export_active_dataset_to_csv_bytes(session: Session) -> bytes:
    dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if dataset is None:
        raise CSVImportValidationError("No active dataset is available to export.")

    stories = session.scalars(
        select(Story)
        .where(Story.dataset_id == dataset.id)
        .options(
            selectinload(Story.trope_links).selectinload(StoryTrope.trope),
            selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
        )
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()

    for story in stories:
        row = {column: clean_text(story.fields_json.get(column, "")) for column in CSV_COLUMNS}
        trope_texts = [
            link.trope.text
            for link in sorted(
                story.trope_links,
                key=lambda item: (
                    item.position is None,
                    item.position if item.position is not None else 0,
                    item.created_at,
                    item.trope.text if item.trope is not None else "",
                ),
            )
            if link.trope is not None
        ]
        keyword_texts = [
            link.keyword.text
            for link in sorted(
                story.keyword_links,
                key=lambda item: (
                    item.position is None,
                    item.position if item.position is not None else 0,
                    item.created_at,
                    item.keyword.text if item.keyword is not None else "",
                ),
            )
            if link.keyword is not None
        ]
        row[TROPE_FIELD] = serialize_tropes(trope_texts)
        row[KEYWORD_FIELD] = serialize_keywords(keyword_texts)
        writer.writerow(row)

    return buffer.getvalue().encode("utf-8-sig")
