#!/usr/bin/env python3
"""Render `costs/sessions.csv` into a cost report and prove cost discipline (QNT-469).

Three checks, each loud-not-silent (exits non-zero with the offending rows):
  - AC2 freshness: every session whose `end` is >48h old must have a `cost_actual_usd`
    (run `make cost-backfill` first if not -- it now covers `reaper-terminated` rows too).
  - AC3 budget: every `cost_actual_usd` must be under the PRD G4 per-session ceiling, and
    every month's Cost Explorer total minus that month's summed session actuals (the idle
    spend) must be under the PRD G4 idle ceiling.
  - AC4 reconciliation: Cost Explorer's all-time `project=hyperlake` total is diffed against
    `sum(cost_actual_usd)` across all sessions; a gap beyond a small tolerance is treated as
    unattributed and fails loud rather than being silently reported.

Writes the full report to `docs/costs.md` and patches the generated block inside README's
`## Cost` section (between `<!-- COST_REPORT:START/END -->` markers).
"""

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import boto3

from scripts.cost_backfill import load_rows

SESSION_CEILING_USD = 2.00  # PRD G4: < $2 per demo session
IDLE_CEILING_USD = 2.00  # PRD G4: < $2/month idle
STALE_AFTER = timedelta(hours=48)
PROJECT_TAG_VALUE = "hyperlake"
CE_REGION = "us-east-1"  # Cost Explorer is a global service with a single endpoint region.
# Before any AWS activity existed, so CE returns 0 for the empty months -- an anchor this
# early is safe and avoids having to track down the exact tag-activation date.
PROJECT_INCEPTION_DATE = date(2026, 1, 1)
# Generous over the known ~$0.62 ad-hoc-Athena-query incident (docs/guides/ops-runbook.md,
# now guarded by `make bronze-query`/QNT-476), plus slack for sessions still <48h pending.
UNATTRIBUTED_GAP_CEILING_USD = 1.00

README_START_MARKER = "<!-- COST_REPORT:START -->"
README_END_MARKER = "<!-- COST_REPORT:END -->"


def query_ce_monthly(ce_client, start_date: str, end_date: str) -> list[dict]:
    """[{"month": "YYYY-MM", "total": float}, ...] tag-filtered on project=hyperlake."""
    resp = ce_client.get_cost_and_usage(
        TimePeriod={"Start": start_date, "End": end_date},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        Filter={"Tags": {"Key": "project", "Values": [PROJECT_TAG_VALUE]}},
    )
    return [
        {
            "month": period["TimePeriod"]["Start"][:7],
            "total": float(period["Total"]["UnblendedCost"]["Amount"]),
        }
        for period in resp["ResultsByTime"]
    ]


def stale_rows(rows: list[dict], now: datetime) -> list[dict]:
    """Sessions whose `end` is >48h old but still lack a cost_actual_usd (AC2)."""
    return [
        row
        for row in rows
        if not row["cost_actual_usd"] and now - datetime.fromisoformat(row["end"]) >= STALE_AFTER
    ]


def over_budget_rows(rows: list[dict]) -> list[dict]:
    """Sessions at or over the per-session ceiling (AC3)."""
    return [
        row
        for row in rows
        if row["cost_actual_usd"] and float(row["cost_actual_usd"]) >= SESSION_CEILING_USD
    ]


def idle_month_report(rows: list[dict], monthly: list[dict]) -> list[dict]:
    """Per month: CE total minus that month's summed session actuals (AC3 idle check)."""
    report = []
    for period in monthly:
        # Still-pending rows (blank cost_actual_usd) are excluded, not counted as 0 spend --
        # their real cost is simply unknown yet, which inflates this month's idle figure until
        # the next `make cost-backfill` fills them in (self-corrects, doesn't silently hide cost).
        session_total = sum(
            float(row["cost_actual_usd"])
            for row in rows
            if row["cost_actual_usd"] and row["start"][:7] == period["month"]
        )
        report.append(
            {
                "month": period["month"],
                "ce_total": period["total"],
                "session_total": session_total,
                "idle": period["total"] - session_total,
            }
        )
    return report


def reconcile(rows: list[dict], monthly: list[dict]) -> dict:
    """CE all-time total vs. sum(cost_actual_usd) across all sessions (AC4)."""
    ce_total = sum(period["total"] for period in monthly)
    session_sum = sum(float(row["cost_actual_usd"]) for row in rows if row["cost_actual_usd"])
    return {"ce_total": ce_total, "session_sum": session_sum, "gap": ce_total - session_sum}


def is_gap_unattributed(gap: float) -> bool:
    """True once |gap| exceeds tolerance in either direction (AC4).

    A positive gap beyond tolerance means real spend CE saw isn't in sessions.csv (the
    ad-hoc-query shape this AC was written to catch). A negative gap that large would mean
    sessions.csv sums to more than CE has ever billed for this tag -- not explained by CE's
    ~24h billing lag alone, so it's treated as equally unattributed.
    """
    return abs(gap) > UNATTRIBUTED_GAP_CEILING_USD


def _active_months(idle_report: list[dict]) -> list[dict]:
    """Months with real Cost Explorer or session activity (skips pre-project $0.00 noise)."""
    return [period for period in idle_report if period["ce_total"] or period["session_total"]]


def _reconciliation_cause(gap: float) -> str:
    if gap > 0:
        return (
            "Known cause: ad-hoc Athena queries against `bronze.trades_raw` without a `dt` "
            "bound (pre-QNT-476) generated S3 request-count cost that never appeared in any "
            "session row -- see `docs/guides/ops-runbook.md`. That path is now guarded by "
            "`make bronze-query`. Any remaining positive gap is expected to shrink toward zero "
            "as sessions finalize."
        )
    return (
        "A negative gap (sessions.csv sums to more than Cost Explorer's current total) is "
        "expected right after a session ends -- Cost Explorer lags actual billing by up to "
        "~24h (PRD FR-7) -- and self-corrects on the next `make cost-report` run once CE "
        "catches up."
    )


def render_markdown(
    rows: list[dict], idle_report: list[dict], reconciliation: dict, now: datetime
) -> str:
    finals = [float(row["cost_actual_usd"]) for row in rows if row["cost_actual_usd"]]
    lines = [
        "# Cost Report",
        "",
        f"Generated by `make cost-report` on {now.date().isoformat()}. Schema: "
        "[costs/README.md](../costs/README.md).",
        "",
        "## Sessions",
        "",
        "| session_id | start | end | cost_estimate_usd | cost_actual_usd | cost_status |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['session_id']} | {row['start']} | {row['end']} | "
            f"{row['cost_estimate_usd']} | {row['cost_actual_usd'] or '_pending_'} | "
            f"{row['cost_status']} |"
        )
    lines += [
        "",
        "## Summary",
        "",
        f"- Sessions finalized: {len(finals)}/{len(rows)}",
        f"- Average session cost: ${sum(finals) / len(finals):.2f}"
        if finals
        else "- Average session cost: n/a",
        f"- Highest session cost: ${max(finals):.2f}" if finals else "- Highest session cost: n/a",
        f"- Ceiling: < ${SESSION_CEILING_USD:.2f}/session (PRD G4)",
        "",
        "## Idle cost by month (Cost Explorer total minus session actuals)",
        "",
        "| month | CE total | session total | idle |",
        "|---|---|---|---|",
    ]
    active_months = _active_months(idle_report)
    for period in active_months:
        lines.append(
            f"| {period['month']} | ${period['ce_total']:.2f} | ${period['session_total']:.2f} | "
            f"${period['idle']:.2f} |"
        )
    omitted = len(idle_report) - len(active_months)
    lines.append("")
    if omitted:
        lines.append(
            f"({omitted} month(s) before any project spend omitted -- $0.00 CE total, nothing "
            "to reconcile.)"
        )
    lines += [
        f"Ceiling: < ${IDLE_CEILING_USD:.2f}/month idle (PRD G4). Cost Explorer lags actual "
        "billing by up to ~24h (PRD FR-7), so the most recent month can show a small negative "
        "idle number right after a session ends -- it self-corrects on the next run.",
        "",
        "## Reconciliation (AC4)",
        "",
        f"- Cost Explorer all-time total (`project=hyperlake`): ${reconciliation['ce_total']:.2f}",
        f"- `sum(cost_actual_usd)` in `costs/sessions.csv`: ${reconciliation['session_sum']:.2f}",
        f"- Gap: ${reconciliation['gap']:.2f}",
        "",
        _reconciliation_cause(reconciliation["gap"]),
    ]
    return "\n".join(lines) + "\n"


def render_readme_block(rows: list[dict], idle_report: list[dict], reconciliation: dict) -> str:
    finals = [float(row["cost_actual_usd"]) for row in rows if row["cost_actual_usd"]]
    avg = sum(finals) / len(finals) if finals else 0.0
    highest = max(finals) if finals else 0.0
    worst_idle = max((period["idle"] for period in _active_months(idle_report)), default=0.0)
    lines = [
        "| | |",
        "|---|---|",
        f"| Average session cost | ${avg:.2f} |",
        f"| Highest session cost | ${highest:.2f} |",
        f"| Idle cost (highest month, Cost Explorer) | ${worst_idle:.2f}/month |",
        "| Target ceiling | < $2/session, < $2/month idle |",
        f"| Cost Explorer reconciliation gap | ${reconciliation['gap']:.2f} (full detail: "
        "[docs/costs.md](docs/costs.md)) |",
    ]
    return "\n".join(lines)


def patch_readme(readme_path: str, block: str) -> None:
    path = Path(readme_path)
    text = path.read_text()
    start = text.index(README_START_MARKER) + len(README_START_MARKER)
    end = text.index(README_END_MARKER)
    path.write_text(f"{text[:start]}\n{block}\n{text[end:]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", default="costs/sessions.csv")
    p.add_argument("--readme", default="README.md")
    p.add_argument("--out", default="docs/costs.md")
    a = p.parse_args()

    rows = load_rows(a.path)
    now = datetime.now(UTC)

    stale = stale_rows(rows, now)
    if stale:
        ids = ", ".join(row["session_id"] for row in stale)
        raise SystemExit(
            f"cost-report: {len(stale)} session(s) older than 48h still lack cost_actual_usd: "
            f"{ids} -- run `make cost-backfill` first"
        )

    over_budget = over_budget_rows(rows)
    if over_budget:
        detail = ", ".join(f"{row['session_id']}=${row['cost_actual_usd']}" for row in over_budget)
        raise SystemExit(
            f"cost-report: session(s) over the ${SESSION_CEILING_USD:.2f} ceiling: {detail}"
        )

    ce = boto3.client("ce", region_name=CE_REGION)
    end_date = (now.date().replace(day=1) + timedelta(days=32)).replace(day=1)
    monthly = query_ce_monthly(ce, PROJECT_INCEPTION_DATE.isoformat(), end_date.isoformat())

    idle_report = idle_month_report(rows, monthly)
    over_idle = [period for period in idle_report if period["idle"] >= IDLE_CEILING_USD]
    if over_idle:
        detail = ", ".join(f"{period['month']}=${period['idle']:.2f}" for period in over_idle)
        raise SystemExit(
            f"cost-report: idle spend over the ${IDLE_CEILING_USD:.2f}/month ceiling: {detail}"
        )

    reconciliation = reconcile(rows, monthly)
    if is_gap_unattributed(reconciliation["gap"]):
        raise SystemExit(
            f"cost-report: reconciliation gap ${reconciliation['gap']:.2f} exceeds "
            f"±${UNATTRIBUTED_GAP_CEILING_USD:.2f} -- investigate before treating it as explained"
        )

    Path(a.out).write_text(render_markdown(rows, idle_report, reconciliation, now))
    patch_readme(a.readme, render_readme_block(rows, idle_report, reconciliation))

    print(f"wrote {a.out}; reconciliation gap ${reconciliation['gap']:.2f}")


if __name__ == "__main__":
    main()
