"""AC5: `heal.py` must size `silver_lookback_days` from the oldest unhealed gap
(`today - gap_start_date + 1`), never the dbt default (2)."""

from datetime import date

from hyperlake.heal import lookback_days


def test_five_day_old_gap_yields_lookback_at_least_six():
    gaps = [{"start": "2026-09-05T10:00:00Z", "end": "2026-09-05T11:00:00Z", "healed": False}]
    assert lookback_days(gaps, today=date(2026, 9, 10)) == 6


def test_oldest_unhealed_gap_wins_over_a_newer_one():
    gaps = [
        {"start": "2026-09-08T10:00:00Z", "end": "2026-09-08T11:00:00Z", "healed": False},
        {"start": "2026-09-01T10:00:00Z", "end": "2026-09-01T11:00:00Z", "healed": False},
    ]
    assert lookback_days(gaps, today=date(2026, 9, 10)) == 10


def test_already_healed_gaps_are_ignored():
    gaps = [
        {"start": "2026-08-01T10:00:00Z", "end": "2026-08-01T11:00:00Z", "healed": True},
        {"start": "2026-09-08T10:00:00Z", "end": "2026-09-08T11:00:00Z", "healed": False},
    ]
    assert lookback_days(gaps, today=date(2026, 9, 10)) == 3


def test_no_unhealed_gaps_yields_zero():
    gaps = [{"start": "2026-09-08T10:00:00Z", "end": "2026-09-08T11:00:00Z", "healed": True}]
    assert lookback_days(gaps, today=date(2026, 9, 10)) == 0
