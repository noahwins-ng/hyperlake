#!/usr/bin/env python3
"""Fill `cost_actual_usd` for costs/sessions.csv rows once Cost Explorer data is ready.

Every `pending` row whose `end` is more than 24h old gets a Cost Explorer query (daily
granularity, filtered on the `project=hyperlake` cost allocation tag, the tag lags billing
data by up to 24h, PRD FR-7) and is rewritten `final` with the summed cost. Already-`final`
rows are never re-queried, so re-running is safe.
"""

import argparse
import csv
from datetime import UTC, datetime, timedelta

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


def query_cost(ce_client, start: str, end: str) -> float:
    """Sum UnblendedCost over the session's date range, tag-filtered on project=hyperlake."""
    start_date = datetime.fromisoformat(start).date()
    end_date = datetime.fromisoformat(end).date() + timedelta(days=1)  # CE End is exclusive
    resp = ce_client.get_cost_and_usage(
        TimePeriod={"Start": start_date.isoformat(), "End": end_date.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Tags": {"Key": "project", "Values": [PROJECT_TAG_VALUE]}},
    )
    return sum(float(day["Total"]["UnblendedCost"]["Amount"]) for day in resp["ResultsByTime"])


def backfill(rows: list[dict], ce_client, now: datetime) -> int:
    """Rewrite eligible pending/reaper-terminated rows in place; return how many were filled.

    A reaped session's `end` is still real (the reap marker's timestamp), so it's just as
    queryable as a normal teardown -- but its `cost_status` stays `reaper-terminated` rather
    than flipping to `final`, so a reap remains visibly distinct in the cost log (see
    docs/guides/ops-runbook.md).
    """
    filled = 0
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
        cost = query_cost(ce_client, row["start"], row["end"])
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
