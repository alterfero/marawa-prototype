"""Normalization helpers for the legacy ``territory`` story field."""

from __future__ import annotations

from collections.abc import Iterable

from app.core.parsing import clean_text, dedupe_preserve_order, normalize_text


def normalize_territories(values: Iterable[object] | None) -> list[str]:
    """Clean territory labels while retaining their display form and order."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return dedupe_preserve_order(values)


def territory_key(value: object) -> str:
    """Return the comparison key used for territory access checks."""
    return normalize_text(clean_text(value))
