"""Shared CSV reader limits, synchronized across concurrent uploads."""
import csv
from contextlib import contextmanager
from threading import Lock

_CSV_FIELD_LIMIT_LOCK = Lock()
_MAX_CSV_FIELD_SIZE = 64 * 1024 * 1024


@contextmanager
def allow_large_csv_fields():
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

