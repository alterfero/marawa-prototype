"""Validation helpers for manually entered story dates."""

from __future__ import annotations

from datetime import date
import re


ISO_CALENDAR_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_iso_calendar_date(value: str) -> bool:
    """Return whether ``value`` is a real ISO-8601 calendar date.

    ``date.fromisoformat`` rejects impossible dates, while the expression keeps
    the accepted representation stable for CSV data (``YYYY-MM-DD`` only).
    """

    if not ISO_CALENDAR_DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
