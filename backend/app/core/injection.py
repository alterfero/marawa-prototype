"""Read-only CSV diagnostics and deterministic duplicate matching."""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.csv_reader import allow_large_csv_fields
from app.core.csv_schema import (
    CSV_COLUMNS, CSV_IMPORT_ALIASES, DATE_OF_RECORDING_FIELD, FULL_EXPORT_COLUMNS,
    KEYWORD_FIELD, MARAWA_STORY_METADATA_FIELD, MARAWA_TERM_CATALOG_FIELD, THEME_FIELD, TROPE_FIELD,
)
from app.core.coordinates import parse_space_coord
from app.core.dates import format_year_interval, parse_year_interval
from app.core.parsing import clean_text, normalize_text, split_keywords, split_themes, split_tropes

TITLE_FIELDS = ("Story title (Eng)", "Story title (French)", "Story title (other)")
TERM_FIELDS = (("tropes", TROPE_FIELD, split_tropes), ("themes", THEME_FIELD, split_themes), ("keywords", KEYWORD_FIELD, split_keywords))


@dataclass
class InjectionCSV:
    rows: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    is_full: bool = False
    row_count: int = 0

    def issue(self, severity, code, message, suggestion, *, row=None, column=None, column_number=None, value=None):
        self.issues.append({
            "severity": severity, "code": code, "message": message, "suggestion": suggestion,
            "row_number": row.get("row_number") if row else None,
            "line_start": row.get("line_start") if row else 1,
            "line_end": row.get("line_end") if row else 1,
            "column": column, "column_number": column_number or (row.get("column_numbers", {}).get(column) if row else None),
            "value": value[:500] if isinstance(value, str) else value,
        })


def inspect_csv(content: bytes) -> InjectionCSV:
    result = InjectionCSV()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        result.issue("error", "encoding", f"Invalid UTF-8 at byte {exc.start + 1}.", "Save the file as CSV UTF-8 and check it again.")
        return result
    if "\x00" in text:
        result.issue("error", "null_byte", "The file contains null bytes.", "Export a text CSV UTF-8 file, not an Excel workbook or UTF-16 file.")
        return result
    with allow_large_csv_fields():
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        row_number = 0
        line_start = 1
        try:
            headers = [clean_text(value) for value in next(reader, [])]
            if not headers:
                result.issue("error", "missing_header", "No readable CSV header.", "Use the header from a Marawa full or legacy export.")
                return result
            if len(headers) == 1 and any(delimiter in headers[0] for delimiter in (";", "\t")):
                result.issue("error", "delimiter", "The header appears to use a semicolon or tab delimiter.", "Export comma-separated CSV; quote cells containing commas.")
            resolved = [h if h in FULL_EXPORT_COLUMNS else CSV_IMPORT_ALIASES.get(h) for h in headers]
            for column in CSV_COLUMNS:
                if column not in resolved:
                    result.issue("error", "missing_column", f"Required column {column!r} is missing.", "Restore this exact column name, even if its values are empty.", column=column)
            seen = set()
            for index, (header, target) in enumerate(zip(headers, resolved), 1):
                if target is None:
                    result.issue("warning", "ignored_column", f"Column {header!r} will be ignored.", "Move any data you need into a supported column before continuing.", column=header, column_number=index)
                elif target in seen:
                    result.issue("error", "duplicate_column", f"Multiple columns map to {target!r}.", "Keep one column for this field and combine its intended values.", column=header, column_number=index)
                seen.add(target)
            result.is_full = MARAWA_STORY_METADATA_FIELD in resolved or MARAWA_TERM_CATALOG_FIELD in resolved
            if result.is_full and not all(c in resolved for c in (MARAWA_STORY_METADATA_FIELD, MARAWA_TERM_CATALOG_FIELD)):
                result.issue("error", "metadata_columns", "A full export needs both Marawa metadata columns.", "Restore the missing metadata column or use a fresh legacy export.")
            row_number = 0
            while True:
                line_start = reader.line_num + 1
                values = next(reader, None)
                if values is None:
                    break
                row_number += 1
                if not any(clean_text(v) for v in values):
                    continue
                result.row_count += 1
                row = {"row_number": row_number, "line_start": line_start, "line_end": reader.line_num}
                if len(values) != len(headers):
                    result.issue("error", "row_too_long" if len(values) > len(headers) else "row_too_short",
                                 f"Expected {len(headers)} cells but found {len(values)}. Values may be shifted into the wrong columns.",
                                 "Check missing separators and quote cells containing commas or line breaks. Keep trailing empty cells.", row=row,
                                 column_number=min(len(values), len(headers)) + 1)
                    continue
                fields = {c: "" for c in CSV_COLUMNS}
                for column, value in zip(resolved, values):
                    if column in fields:
                        fields[column] = clean_text(value)
                row["column_numbers"] = {c: i + 1 for i, c in enumerate(resolved) if c is not None}
                row["fields"] = fields
                row["metadata"] = None
                row["catalog"] = None
                if result.is_full:
                    for column, key in ((MARAWA_STORY_METADATA_FIELD, "metadata"), (MARAWA_TERM_CATALOG_FIELD, "catalog")):
                        value = values[resolved.index(column)] if column in resolved else ""
                        if not clean_text(value):
                            continue
                        try:
                            parsed = json.loads(value)
                            if not isinstance(parsed, dict):
                                raise ValueError("Expected a JSON object")
                            row[key] = parsed
                        except ValueError as exc:
                            result.issue("error", "metadata_json", f"Invalid metadata JSON: {exc}.", "Restore this metadata cell from the full export; CSV quotes must be doubled.", row=row, column=column, column_number=resolved.index(column) + 1, value=value)
                _inspect_fields(result, row, resolved)
                result.rows.append(row)
        except csv.Error as exc:
            result.issue("error", "csv_syntax", f"CSV parsing stopped near line {reader.line_num}: {exc}.", "Repair quoting here and check the file again; subsequent entries could not be checked.", row={"row_number": row_number + 1, "line_start": line_start, "line_end": reader.line_num})
    if result.row_count == 0:
        result.issue("error", "empty_file", "No story entries were found.", "Add at least one non-empty story row under the header.")
    return result


def _inspect_fields(result: InjectionCSV, row: dict, columns: list):
    fields = row["fields"]
    def issue(severity, code, message, suggestion, column):
        result.issue(severity, code, message, suggestion, row=row, column=column,
                     column_number=columns.index(column) + 1 if column in columns else None, value=fields[column])
    if not any(fields[c] for c in TITLE_FIELDS):
        issue("warning", "missing_title", "This entry has no title in any language.", "Add a title to make the story easier to identify, or acknowledge this warning.", TITLE_FIELDS[0])
    date = fields[DATE_OF_RECORDING_FIELD]
    # Injection accepts explicit years and intervals only; other date text is ignored.
    if re.fullmatch(r"[0-9]{4}", date):
        date = f"[{date}, {date}]"
    interval = parse_year_interval(date)
    fields[DATE_OF_RECORDING_FIELD] = (
        format_year_interval(interval.year1, interval.year2) if interval else ""
    )
    if fields["space coord"] and parse_space_coord(fields["space coord"]) is None:
        issue("warning", "coordinates", "Coordinates are not usable by the map (0,0 is treated as unknown). The text will be preserved.", "Check latitude and longitude, for example 17.5 S, 149.5 W, or leave empty if unknown.", "space coord")
    for column in ("URL ?", "external link"):
        value = fields[column]
        if value:
            try:
                url = urlsplit(value)
                valid = url.scheme in {"http", "https"} and bool(url.netloc)
            except ValueError:
                valid = False
            if not valid:
                issue("warning", "url", "This cell does not look like a complete web URL. The text will be preserved.", "Check for shifted cells or add an http:// or https:// URL.", column)


def duplicate_keys(fields: dict) -> list[tuple[str, str]]:
    """Match a non-empty title AND abstract from the same story.

    Language columns are alternatives. Case and whitespace are ignored, with
    no minimum abstract length. Empty values cannot establish a duplicate.
    """
    titles = dict.fromkeys(normalize_text(fields.get(column, "")) for column in TITLE_FIELDS)
    abstracts = dict.fromkeys(normalize_text(fields.get(column, "")) for column in ("Abstract (Eng)", "Abstract (Fr)"))
    return [(title, abstract) for title in titles if title for abstract in abstracts if abstract]


def prepare_injection_titles(
    fields: dict[str, str], *, candidates: list[dict], used_titles: set[str], reserved_titles: set[str],
) -> dict[str, str]:
    """Number title-only collisions, keeping a stable title for repeated entries.

    Reserved titles include explicit titles later in the CSV, so automatic
    numbering cannot consume one of those names. Repeated incoming entries
    retain the effective title of their earlier numbered duplicate.
    """
    result = dict(fields)
    if candidates:
        for candidate in candidates:
            for column, incoming_title in candidate.get("input_titles", {}).items():
                marker = normalize_text(incoming_title)
                effective_title = candidate["fields"][column]
                if marker and normalize_text(effective_title) != marker:
                    for target_column in TITLE_FIELDS:
                        if normalize_text(result.get(target_column, "")) == marker:
                            result[target_column] = effective_title
        return result

    numbered = {}
    unavailable = used_titles | reserved_titles
    for column in TITLE_FIELDS:
        title = fields.get(column, "")
        marker = normalize_text(title)
        if not marker or marker not in used_titles:
            continue
        if marker not in numbered:
            number = 1
            while normalize_text(f"{title} {number}") in unavailable:
                number += 1
            numbered[marker] = f"{title} {number}"
            unavailable.add(normalize_text(numbered[marker]))
        result[column] = numbered[marker]
    return result
