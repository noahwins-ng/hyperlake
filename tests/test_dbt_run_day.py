"""Pins scripts/dbt_run_day.py: `make dbt-run-day DAY=<day>` computes the dbt vars the README's
run steps used to spell out as escaped JSON, then hands them to gh_run.sh unchanged."""

import json
from datetime import date

from scripts.dbt_run_day import day_vars, gh_run_args


def test_day_vars_cover_the_day_through_today() -> None:
    assert day_vars(date(2026, 9, 10), today=date(2026, 9, 12)) == {
        "silver_lookback_days": 3,
        "freshness_window_start": "2026-09-10 00:00:00",
        "freshness_window_end": "2026-09-11 01:00:00",
    }


def test_day_vars_for_today_is_a_one_day_lookback() -> None:
    assert day_vars(date(2026, 9, 12), today=date(2026, 9, 12))["silver_lookback_days"] == 1


def test_gh_run_args_pass_vars_as_one_json_argument() -> None:
    args = gh_run_args("dbt-run-1-2", date(2026, 9, 10), today=date(2026, 9, 12))

    assert args[:3] == ["./scripts/gh_run.sh", "dbt-run-1-2", "-f"]
    key, _, value = args[3].partition("=")
    assert key == "vars"
    assert json.loads(value)["silver_lookback_days"] == 3
