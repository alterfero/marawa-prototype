from __future__ import annotations

import re

from app.core.parsing import clean_text


COORDINATE_TOKEN_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?", re.IGNORECASE)


def _apply_direction(value: float, direction: str) -> float:
    if direction in {"S", "W"}:
        return -abs(value)
    if direction in {"N", "E"}:
        return abs(value)
    return value


def parse_space_coord(value: str) -> tuple[float, float] | None:
    text = clean_text(value).replace("−", "-")
    if not text:
        return None

    cleaned = (
        text.replace("≈", "")
        .replace("~", "")
        .replace("(", " ")
        .replace(")", " ")
        .replace("[", " ")
        .replace("]", " ")
    )
    cleaned = re.sub(r"(?<=\d),(?=\d)", ".", cleaned)
    matches = COORDINATE_TOKEN_RE.findall(cleaned)
    if len(matches) < 2:
        return None

    latitude = _apply_direction(float(matches[0][0]), matches[0][1].upper())
    longitude = _apply_direction(float(matches[1][0]), matches[1][1].upper())
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if latitude == 0.0 and longitude == 0.0:
        # In this corpus, 0,0 is a placeholder for unknown location rather than a real point.
        return None
    return latitude, longitude
