import pytest

from app.core.dates import (
    YearInterval,
    extract_legacy_year_interval,
    format_year_interval,
    is_valid_year_interval,
    normalize_imported_year_interval,
    parse_year_interval,
)


def test_year_interval_parses_and_formats_canonical_csv_values() -> None:
    interval = parse_year_interval("[1971, 1980]", require_increasing=True)

    assert interval == YearInterval(year1=1971, year2=1980)
    assert format_year_interval(interval.year1, interval.year2) == "[1971, 1980]"


@pytest.mark.parametrize(
    "value",
    ["[2050, 2051]", "[1799, 1801]", "[1980, 1971]", "[1971, 1971]", "1971-1980", ""],
)
def test_new_year_interval_requires_an_increasing_supported_range(value: str) -> None:
    assert is_valid_year_interval(value) is False


def test_legacy_single_date_migrates_to_a_same_year_interval() -> None:
    assert extract_legacy_year_interval("4 March 1998") == YearInterval(year1=1998, year2=1998)
    assert normalize_imported_year_interval("2024-02-29") == YearInterval(year1=2024, year2=2024)
    assert parse_year_interval("[1998, 1998]") == YearInterval(year1=1998, year2=1998)
