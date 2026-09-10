#!/usr/bin/env python3
"""Ad-hoc Athena query wrapper for `bronze.trades_raw` (QNT-476). Requires a bounded `dt`
lower bound and refuses to run unfiltered: the table's partition projection has no
persisted partition metadata, so an unbounded query makes Athena issue an S3 LIST per
virtual partition (~4,090 across the watchlist) -- see docs/guides/ops-runbook.md.
"""

import argparse
import time

import boto3

REGION = "ap-northeast-1"
WORKGROUP = "hyperlake"
DATABASE = "bronze"
POLL_INTERVAL_SECONDS = 1


def build_query(dt_from: str, dt_to: str, select: str, extra_where: str | None, limit: int) -> str:
    where = [f"dt >= DATE '{dt_from}'", f"dt <= DATE '{dt_to}'"]
    if extra_where:
        where.append(f"({extra_where})")
    return f"SELECT {select} FROM {DATABASE}.trades_raw WHERE {' AND '.join(where)} LIMIT {limit}"


def run_query(athena_client, sql: str) -> list[dict]:
    query_id = athena_client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]

    while True:
        execution = athena_client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = execution["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"{sql!r} {state}: {reason}")
        time.sleep(POLL_INTERVAL_SECONDS)

    rows = athena_client.get_query_results(QueryExecutionId=query_id)["ResultSet"]["Rows"]
    if len(rows) < 1:
        return []
    columns = [d["VarCharValue"] for d in rows[0]["Data"]]
    return [
        dict(zip(columns, (d.get("VarCharValue") for d in row["Data"]), strict=True))
        for row in rows[1:]
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dt-from",
        required=True,
        help="dt lower bound, YYYY-MM-DD -- required, refuses to run unbounded",
    )
    parser.add_argument("--dt-to", help="dt upper bound, YYYY-MM-DD (default: --dt-from, one day)")
    parser.add_argument("--select", default="*", help="columns to select (default: *)")
    parser.add_argument("--where", help="extra WHERE clause, ANDed with the dt bound")
    parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    sql = build_query(args.dt_from, args.dt_to or args.dt_from, args.select, args.where, args.limit)
    print(f"query: {sql}")
    athena = boto3.client("athena", region_name=REGION)
    rows = run_query(athena, sql)
    for row in rows:
        print(row)
    print(f"{len(rows)} row(s)")


if __name__ == "__main__":
    main()
