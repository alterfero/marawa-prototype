"""Core backend utilities and configuration."""

from app.core.coordinates import parse_space_coord
from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.core.parsing import (
    clean_text,
    dedupe_preserve_order,
    normalize_text,
    serialize_keywords,
    serialize_tropes,
    split_keywords,
    split_tropes,
)

__all__ = [
    "CSV_COLUMNS",
    "KEYWORD_FIELD",
    "TROPE_FIELD",
    "clean_text",
    "dedupe_preserve_order",
    "normalize_text",
    "parse_space_coord",
    "serialize_keywords",
    "serialize_tropes",
    "split_keywords",
    "split_tropes",
]
