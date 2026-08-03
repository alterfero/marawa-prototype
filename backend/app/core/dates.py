"""Parsing and validation helpers for story recording-year intervals."""

from __future__ import annotations

from dataclasses import dataclass
import re


MIN_RECORDING_YEAR = 1800
MAX_RECORDING_YEAR = 2050
YEAR_INTERVAL_RE = re.compile(r"^\[\s*(\d{4})\s*,\s*(\d{4})\s*\]$")
LEGACY_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


@dataclass(frozen=True)
class YearInterval:
    """An inclusive interval represented by two recording years."""

    year1: int
    year2: int


def format_year_interval(year1: int, year2: int) -> str:
    """Return the canonical legacy-CSV representation for a year interval."""

    return f"[{year1}, {year2}]"


def parse_year_interval(value: str, *, require_increasing: bool = False) -> YearInterval | None:
    """Parse a canonical ``[year1, year2]`` interval within supported bounds.

    Equal years are retained for migrated legacy records, but newly entered or
    edited dates pass ``require_increasing=True``.
    """

    match = YEAR_INTERVAL_RE.fullmatch(value.strip())
    if match is None:
        return None

    year1, year2 = (int(part) for part in match.groups())
    if not (MIN_RECORDING_YEAR <= year1 <= MAX_RECORDING_YEAR):
        return None
    if not (MIN_RECORDING_YEAR <= year2 <= MAX_RECORDING_YEAR):
        return None
    if year2 < year1 or (require_increasing and year2 <= year1):
        return None
    return YearInterval(year1=year1, year2=year2)


def extract_legacy_year_interval(value: str) -> YearInterval | None:
    """Extract the first valid year from an older free-form recording date.

    Historical one-date values migrate to a same-year interval so no known year
    is discarded. Values with no supported four-digit year remain unset.
    """

    match = LEGACY_YEAR_RE.search(value)
    if match is None:
        return None
    year = int(match.group(1))
    if not MIN_RECORDING_YEAR <= year <= MAX_RECORDING_YEAR:
        return None
    return YearInterval(year1=year, year2=year)


def normalize_imported_year_interval(value: str) -> YearInterval | None:
    """Read new CSV intervals and gracefully migrate older one-date CSV values."""

    text = value.strip()
    if not text:
        return None
    return parse_year_interval(text) or extract_legacy_year_interval(text)


def is_valid_year_interval(value: str, *, require_increasing: bool = True) -> bool:
    """Return whether a value is a supported canonical recording-year interval."""

    return parse_year_interval(value, require_increasing=require_increasing) is not None
