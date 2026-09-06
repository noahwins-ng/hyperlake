import pytest

from hyperlake.backfill.hour_list import hour_list


def test_single_day_returns_24_hours_plus_trailing_hour():
    hours = hour_list("2026-09-03", "2026-09-03")
    assert len(hours) == 25
    assert hours[0] == {"date": "20260903", "hour": "0"}
    assert hours[23] == {"date": "20260903", "hour": "23"}
    # Trailing H+1: the boundary rule (spike "Hour-file boundary") means a fill whose
    # event `time` falls in TO's last hour may only land in the next day's hour-0 file.
    assert hours[24] == {"date": "20260904", "hour": "0"}


def test_multi_day_range_is_inclusive_of_both_ends():
    hours = hour_list("2026-09-03", "2026-09-04")
    assert len(hours) == 24 + 24 + 1
    assert hours[-1] == {"date": "20260905", "hour": "0"}


def test_rejects_to_before_from():
    with pytest.raises(ValueError):
        hour_list("2026-09-04", "2026-09-03")
