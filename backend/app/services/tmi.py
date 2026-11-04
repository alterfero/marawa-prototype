"""Read-only reference data, independent of the active Marawa dataset."""

from functools import lru_cache
import hashlib
from pathlib import Path

from app.core.config import REPO_ROOT
from app.core.tmi import build_tmi_catalogue

TMI_DIRECTORY = REPO_ROOT / "legacy_tropes"


@lru_cache(maxsize=2)
def _load_catalogue(directory: Path, csv_version: int, csv_size: int) -> dict:
    raw = (directory / "tmi.csv").read_bytes()
    result = build_tmi_catalogue(raw.decode("utf-8-sig"))
    result["source"] = {
        "filename": "tmi.csv",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "title": "Katja Mellmann: Thompson’s Motif-Index as CSV File (2020)",
        "url": "https://github.com/KatjaMellmann/TMI_as_CSV",
        "doi": "https://doi.org/10.17605/OSF.IO/XEB67",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
    }
    return result


def get_tmi_catalogue() -> dict:
    source_stat = (TMI_DIRECTORY / "tmi.csv").stat()
    return _load_catalogue(
        TMI_DIRECTORY,
        source_stat.st_mtime_ns,
        source_stat.st_size,
    )
