"""AC1: gap -> hour-file expansion pins the boundary rule (trailing H+1) and a
cross-midnight gap spanning two dates."""

from datetime import UTC, datetime

import pytest

from hyperlake.heal import gap_to_hours


def test_gap_within_one_hour_pair_includes_trailing_hour():
    hours = gap_to_hours(
        datetime(2026, 9, 8, 12, 58, tzinfo=UTC), datetime(2026, 9, 8, 13, 2, tzinfo=UTC)
    )
    assert hours == [
        {"date": "20260908", "hour": "12"},
        {"date": "20260908", "hour": "13"},
        {"date": "20260908", "hour": "14"},
    ]


def test_cross_midnight_gap_spans_two_dates_plus_trailing_hour():
    hours = gap_to_hours(
        datetime(2026, 9, 8, 23, 50, tzinfo=UTC), datetime(2026, 9, 9, 0, 10, tzinfo=UTC)
    )
    assert hours == [
        {"date": "20260908", "hour": "23"},
        {"date": "20260909", "hour": "0"},
        {"date": "20260909", "hour": "1"},
    ]


def test_gap_exactly_on_hour_boundaries_still_gets_trailing_hour():
    hours = gap_to_hours(
        datetime(2026, 9, 8, 10, 0, tzinfo=UTC), datetime(2026, 9, 8, 11, 0, tzinfo=UTC)
    )
    assert hours == [
        {"date": "20260908", "hour": "10"},
        {"date": "20260908", "hour": "11"},
    ]


def test_rejects_gap_end_before_or_equal_to_start():
    same = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        gap_to_hours(same, same)
