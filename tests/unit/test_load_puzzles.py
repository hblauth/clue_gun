import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from services.clue_indexer.load_puzzles import parse_date


def test_parse_date_basic():
    assert parse_date("15th May 2013 at 9:14 AM") == date(2013, 5, 15)


def test_parse_date_1st():
    assert parse_date("1st January 2020 at 8:00 AM") == date(2020, 1, 1)


def test_parse_date_2nd():
    assert parse_date("2nd April 2020 at 1:43 AM") == date(2020, 4, 2)


def test_parse_date_3rd():
    assert parse_date("3rd March 2021 at 6:00 AM") == date(2021, 3, 3)


def test_parse_date_21st():
    assert parse_date("21st February 2023 at 1:25 AM") == date(2023, 2, 21)


def test_parse_date_22nd():
    assert parse_date("22nd October 2012 at 10:17 AM") == date(2012, 10, 22)


def test_parse_date_empty():
    assert parse_date("") is None


def test_parse_date_none_like():
    assert parse_date(None) is None


def test_parse_date_invalid():
    assert parse_date("not a date") is None


def test_parse_date_all_months():
    months = [
        ("January", 1), ("February", 2), ("March", 3), ("April", 4),
        ("May", 5), ("June", 6), ("July", 7), ("August", 8),
        ("September", 9), ("October", 10), ("November", 11), ("December", 12),
    ]
    for month_name, month_num in months:
        result = parse_date(f"5th {month_name} 2022 at 9:00 AM")
        assert result == date(2022, month_num, 5), f"Failed for {month_name}"
