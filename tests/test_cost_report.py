from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from scripts.cost_report import (
    UNATTRIBUTED_GAP_CEILING_USD,
    idle_month_report,
    is_gap_unattributed,
    over_budget_rows,
    query_ce_monthly,
    reconcile,
    render_markdown,
    render_readme_block,
    stale_rows,
)


def _row(session_id: str, start: str, end: str, actual: str = "", status: str = "pending") -> dict:
    return {
        "session_id": session_id,
        "start": start,
        "end": end,
        "cost_estimate_usd": "0.10",
        "cost_actual_usd": actual,
        "cost_status": status,
        "ce_query_date": "2026-09-10" if actual else "",
    }


def test_stale_rows_flags_old_blank_rows() -> None:
    now = datetime.now(UTC)
    old_end = (now - timedelta(hours=49)).isoformat()
    recent_end = (now - timedelta(hours=10)).isoformat()
    rows = [
        _row("old-blank", old_end, old_end),
        _row("old-final", old_end, old_end, actual="0.25", status="final"),
        _row("recent-blank", recent_end, recent_end),
    ]

    stale = stale_rows(rows, now)

    assert [r["session_id"] for r in stale] == ["old-blank"]


def test_over_budget_rows_flags_sessions_at_or_over_ceiling() -> None:
    rows = [
        _row(
            "cheap", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z", actual="0.29", status="final"
        ),
        _row(
            "expensive",
            "2026-09-01T00:00:00Z",
            "2026-09-01T00:10:00Z",
            actual="2.00",
            status="final",
        ),
        _row("pending", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z"),
    ]

    over = over_budget_rows(rows)

    assert [r["session_id"] for r in over] == ["expensive"]


def test_idle_month_report_subtracts_session_totals_from_ce_total() -> None:
    rows = [
        _row("s1", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z", actual="0.30", status="final"),
        _row("s2", "2026-09-05T00:00:00Z", "2026-09-05T00:10:00Z", actual="0.20", status="final"),
    ]
    monthly = [{"month": "2026-08", "total": 0.0}, {"month": "2026-09", "total": 1.00}]

    report = idle_month_report(rows, monthly)

    assert report == [
        {"month": "2026-08", "ce_total": 0.0, "session_total": 0.0, "idle": 0.0},
        {"month": "2026-09", "ce_total": 1.00, "session_total": 0.50, "idle": 0.50},
    ]


def test_reconcile_sums_ce_total_against_session_actuals() -> None:
    rows = [
        _row("s1", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z", actual="0.30", status="final"),
        _row("s2", "2026-09-05T00:00:00Z", "2026-09-05T00:10:00Z"),  # still pending -> counts as 0
    ]
    monthly = [{"month": "2026-09", "total": 1.00}]

    result = reconcile(rows, monthly)

    assert result == {"ce_total": 1.00, "session_sum": 0.30, "gap": 0.70}


def test_is_gap_unattributed_is_symmetric_around_zero() -> None:
    within = UNATTRIBUTED_GAP_CEILING_USD - 0.01
    over = UNATTRIBUTED_GAP_CEILING_USD + 0.01

    assert is_gap_unattributed(within) is False
    assert is_gap_unattributed(-within) is False
    assert is_gap_unattributed(over) is True
    assert is_gap_unattributed(-over) is True


def test_query_ce_monthly_shapes_the_ce_response() -> None:
    ce = MagicMock()
    ce.get_cost_and_usage.return_value = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-09-01", "End": "2026-10-01"},
                "Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}},
            },
        ]
    }

    monthly = query_ce_monthly(ce, "2026-01-01", "2026-10-01")

    assert monthly == [{"month": "2026-09", "total": 1.23}]
    ce.get_cost_and_usage.assert_called_once_with(
        TimePeriod={"Start": "2026-01-01", "End": "2026-10-01"},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        Filter={"Tags": {"Key": "project", "Values": ["hyperlake"]}},
    )


def test_render_markdown_includes_sessions_and_reconciliation() -> None:
    rows = [
        _row("s1", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z", actual="0.30", status="final")
    ]
    idle_report = [{"month": "2026-09", "ce_total": 1.00, "session_total": 0.30, "idle": 0.70}]
    reconciliation = {"ce_total": 1.00, "session_sum": 0.30, "gap": 0.70}
    now = datetime(2026, 9, 13, tzinfo=UTC)

    md = render_markdown(rows, idle_report, reconciliation, now)

    assert "s1" in md
    assert "Reconciliation" in md
    assert "$0.70" in md


def test_render_readme_block_includes_average_and_gap() -> None:
    rows = [
        _row("s1", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z", actual="0.30", status="final")
    ]
    idle_report = [{"month": "2026-09", "ce_total": 1.00, "session_total": 0.30, "idle": 0.70}]
    reconciliation = {"ce_total": 1.00, "session_sum": 0.30, "gap": 0.70}

    block = render_readme_block(rows, idle_report, reconciliation)

    assert "$0.30" in block
    assert "$0.70" in block


def test_render_readme_block_never_shows_a_negative_headline_number() -> None:
    rows = [
        _row("s1", "2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z", actual="0.30", status="final")
    ]
    idle_report = [{"month": "2026-09", "ce_total": 0.10, "session_total": 0.30, "idle": -0.20}]
    reconciliation = {"ce_total": 0.10, "session_sum": 0.30, "gap": -0.20}

    block = render_readme_block(rows, idle_report, reconciliation)

    assert "$-" not in block
    assert "$0.00/month" in block
    assert "-$0.20" in block
