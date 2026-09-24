#!/usr/bin/env python3
"""Fill `cost_actual_usd` for costs/sessions.csv rows once Cost Explorer data is ready.

Every `pending` row whose `end` is more than 24h old gets a Cost Explorer query (daily
granularity, filtered on the `project=hyperlake` cost allocation tag, the tag lags billing
data by up to 24h, PRD FR-7) and is rewritten `final` with its share of the cost. Daily is
the finest granularity Cost Explorer offers, so each day's total is split across every
session in the CSV that overlaps that day, weighted by overlap time; giving every
same-day session the whole day's total would count that day once per session.
Already-`final` rows are never re-queried, so re-running is safe.
"""

import argparse
import csv
from datetime import UTC, date, datetime, timedelta

import boto3

CSV_PATH = "costs/sessions.csv"
FIELDNAMES = [
    "session_id",
    "start",
    "end",
    "cost_estimate_usd",
    "cost_actual_usd",
    "cost_status",
    "ce_query_date",
]
PROJECT_TAG_VALUE = "hyperlake"
BACKFILL_DELAY = timedelta(hours=24)
CE_REGION = "us-east-1"  # Cost Explorer is a global service with a single endpoint region.


def load_rows(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def query_day_cost(ce_client, day: date) -> float:
    """UnblendedCost for one UTC day, tag-filtered on project=hyperlake."""
    resp = ce_client.get_cost_and_usage(
        TimePeriod={"Start": day.isoformat(), "End": (day + timedelta(days=1)).isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Tags": {"Key": "project", "Values": [PROJECT_TAG_VALUE]}},
    )
    return sum(float(d["Total"]["UnblendedCost"]["Amount"]) for d in resp["ResultsByTime"])


def _days(row: dict) -> list[date]:
    start = datetime.fromisoformat(row["start"]).date()
    end = datetime.fromisoformat(row["end"]).date()
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def _overlap_seconds(row: dict, day: date) -> float:
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    start = max(datetime.fromisoformat(row["start"]), day_start)
    end = min(datetime.fromisoformat(row["end"]), day_start + timedelta(days=1))
    return max((end - start).total_seconds(), 0.0)


def session_share(row: dict, day: date, rows: list[dict]) -> float:
    """This row's fraction of `day`'s cost: its overlap time over the summed overlap of
    every row touching that day (an even split if every overlap is zero-length)."""
    sharing = [r for r in rows if day in _days(r)]
    total = sum(_overlap_seconds(r, day) for r in sharing)
    if total == 0:
        return 1 / len(sharing)
    return _overlap_seconds(row, day) / total


def backfill(rows: list[dict], ce_client, now: datetime) -> int:
    """Rewrite eligible pending/reaper-terminated rows in place; return how many were filled.

    A reaped session's `end` is still real (the reap marker's timestamp), so it's just as
    queryable as a normal teardown -- but its `cost_status` stays `reaper-terminated` rather
    than flipping to `final`, so a reap remains visibly distinct in the cost log (see
    docs/guides/ops-runbook.md).
    """
    filled = 0
    day_costs: dict[date, float] = {}
    for row in rows:
        if row["cost_status"] not in ("pending", "reaper-terminated"):
            continue
        if row["cost_actual_usd"]:
            # A reaper-terminated row never flips to `final`, so `cost_status` alone can't
            # signal "already filled" the way it does for pending -> final -- check the value.
            continue
        end = datetime.fromisoformat(row["end"])
        if now - end < BACKFILL_DELAY:
            continue
        cost = 0.0
        for day in _days(row):
            if day not in day_costs:
                day_costs[day] = query_day_cost(ce_client, day)
            cost += day_costs[day] * session_share(row, day, rows)
        row["cost_actual_usd"] = f"{cost:.2f}"
        if row["cost_status"] == "pending":
            row["cost_status"] = "final"
        row["ce_query_date"] = now.date().isoformat()
        filled += 1
        print(f"{row['session_id']}: cost_actual_usd={row['cost_actual_usd']}")
    return filled


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", default=CSV_PATH)
    p.add_argument("--dry-run", action="store_true", help="query and print, don't write the CSV")
    a = p.parse_args()

    rows = load_rows(a.path)
    ce = boto3.client("ce", region_name=CE_REGION)
    now = datetime.now(UTC)
    filled = backfill(rows, ce, now)
    if filled and not a.dry_run:
        write_rows(a.path, rows)

    suffix = " (dry-run, not written)" if a.dry_run else ""
    print(f"{filled} row(s) backfilled{suffix}")


if __name__ == "__main__":
    main()
