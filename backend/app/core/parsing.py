from __future__ import annotations

import re
from typing import Iterable


WHITESPACE_RE = re.compile(r"\s+")
KEYWORD_SPLIT_RE = re.compile(r"[;\n]+")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def normalize_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", clean_text(value)).strip().lower()


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = clean_text(value)
        if not item:
            continue
        marker = normalize_text(item)
        if marker in seen:
            continue
        seen.add(marker)
        cleaned.append(item)
    return cleaned


def split_keywords(value: str) -> list[str]:
    parts = [clean_text(piece) for piece in KEYWORD_SPLIT_RE.split(clean_text(value))]
    return dedupe_preserve_order(parts)


def serialize_keywords(values: Iterable[str]) -> str:
    return " ; ".join(dedupe_preserve_order(values))


def split_tropes(value: str) -> list[str]:
    text = clean_text(value).replace("\r\n", "\n")
    if not text:
        return []
    if "§§" in text:
        pieces = [piece.strip(" \n;") for piece in text.split("§§")]
    else:
        pieces = [piece.strip(" \n;") for piece in re.split(r"[;\n]+", text)]
    return dedupe_preserve_order(pieces)


def serialize_tropes(values: Iterable[str]) -> str:
    items = dedupe_preserve_order(values)
    if not items:
        return ""
    return "\n".join(f"§§ {item}" for item in items)

