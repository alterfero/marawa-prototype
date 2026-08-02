import pytest

from app.core.dates import is_iso_calendar_date


@pytest.mark.parametrize("value", ["2024-02-29", "1998-03-04"])
def test_is_iso_calendar_date_accepts_real_iso_dates(value: str) -> None:
    assert is_iso_calendar_date(value) is True


@pytest.mark.parametrize("value", ["2023-02-29", "2024-13-01", "2024-2-01", "04/03/1998", ""])
def test_is_iso_calendar_date_rejects_invalid_or_non_iso_dates(value: str) -> None:
    assert is_iso_calendar_date(value) is False
