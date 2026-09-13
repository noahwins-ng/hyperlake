from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from scripts.cost_backfill import backfill


def _row(session_id: str, end: str, status: str = "pending") -> dict:
    return {
        "session_id": session_id,
        "start": end,
        "end": end,
        "cost_estimate_usd": "1.23",
        "cost_actual_usd": "" if status == "pending" else "1.00",
        "cost_status": status,
        "ce_query_date": "" if status == "pending" else "2026-09-01",
    }


def _mock_ce(amount: str) -> MagicMock:
    ce = MagicMock()
    ce.get_cost_and_usage.return_value = {
        "ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}}}]
    }
    return ce


def test_pending_row_older_than_24h_becomes_final() -> None:
    now = datetime.now(UTC)
    old_end = (now - timedelta(hours=30)).isoformat()
    rows = [_row("s1", old_end)]
    ce = _mock_ce("0.42")

    filled = backfill(rows, ce, now)

    assert filled == 1
    assert rows[0]["cost_status"] == "final"
    assert rows[0]["cost_actual_usd"] == "0.42"
    assert rows[0]["ce_query_date"] == now.date().isoformat()
    end_date = datetime.fromisoformat(old_end).date()
    ce.get_cost_and_usage.assert_called_once_with(
        TimePeriod={
            "Start": end_date.isoformat(),
            "End": (end_date + timedelta(days=1)).isoformat(),
        },
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Tags": {"Key": "project", "Values": ["hyperlake"]}},
    )


def test_pending_row_younger_than_24h_is_left_pending() -> None:
    now = datetime.now(UTC)
    recent_end = (now - timedelta(hours=2)).isoformat()
    rows = [_row("s2", recent_end)]
    ce = _mock_ce("0.00")

    filled = backfill(rows, ce, now)

    assert filled == 0
    assert rows[0]["cost_status"] == "pending"
    ce.get_cost_and_usage.assert_not_called()


def test_final_row_is_never_requeried() -> None:
    now = datetime.now(UTC)
    old_end = (now - timedelta(hours=30)).isoformat()
    rows = [_row("s3", old_end, status="final")]
    ce = _mock_ce("9.99")

    filled = backfill(rows, ce, now)

    assert filled == 0
    assert rows[0]["cost_actual_usd"] == "1.00"
    ce.get_cost_and_usage.assert_not_called()


def test_reaper_terminated_row_older_than_24h_gets_actual_but_keeps_status() -> None:
    now = datetime.now(UTC)
    old_end = (now - timedelta(hours=30)).isoformat()
    rows = [
        {
            "session_id": "s5",
            "start": old_end,
            "end": old_end,
            "cost_estimate_usd": "0.03",
            "cost_actual_usd": "",
            "cost_status": "reaper-terminated",
            "ce_query_date": "",
        }
    ]
    ce = _mock_ce("0.07")

    filled = backfill(rows, ce, now)

    assert filled == 1
    assert rows[0]["cost_status"] == "reaper-terminated"
    assert rows[0]["cost_actual_usd"] == "0.07"
    assert rows[0]["ce_query_date"] == now.date().isoformat()


def test_reaper_terminated_row_is_never_requeried_once_filled() -> None:
    now = datetime.now(UTC)
    old_end = (now - timedelta(hours=30)).isoformat()
    rows = [
        {
            "session_id": "s6",
            "start": old_end,
            "end": old_end,
            "cost_estimate_usd": "0.03",
            "cost_actual_usd": "",
            "cost_status": "reaper-terminated",
            "ce_query_date": "",
        }
    ]
    ce = _mock_ce("0.07")

    first = backfill(rows, ce, now)
    second = backfill(rows, ce, now)

    assert first == 1
    assert second == 0
    assert rows[0]["cost_status"] == "reaper-terminated"
    assert rows[0]["cost_actual_usd"] == "0.07"
    ce.get_cost_and_usage.assert_called_once()


def test_rerun_is_idempotent() -> None:
    now = datetime.now(UTC)
    old_end = (now - timedelta(hours=30)).isoformat()
    rows = [_row("s4", old_end)]
    ce = _mock_ce("2.50")

    first = backfill(rows, ce, now)
    second = backfill(rows, ce, now)

    assert first == 1
    assert second == 0
    ce.get_cost_and_usage.assert_called_once()
