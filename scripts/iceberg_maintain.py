#!/usr/bin/env python3
"""OPTIMIZE + VACUUM every Iceberg table in the silver (and gold, once it exists) Glue
databases, via Athena (QNT-454, PRD §5 Iceberg maintenance). A database that doesn't exist
yet (gold lands in a later phase) is skipped rather than treated as an error.
"""

import time

import boto3

REGION = "ap-northeast-1"
WORKGROUP = "hyperlake"
DATABASES = ["silver", "gold"]
POLL_INTERVAL_SECONDS = 1
QUERY_TIMEOUT_SECONDS = 900  # a hung query fails loud instead of blocking the script forever


def iceberg_tables(glue_client, database: str) -> list[str]:
    """Names of Iceberg tables in `database`; [] if the database doesn't exist yet."""
    try:
        paginator = glue_client.get_paginator("get_tables")
        return [
            t["Name"]
            for page in paginator.paginate(DatabaseName=database)
            for t in page["TableList"]
            if t.get("Parameters", {}).get("table_type", "").upper() == "ICEBERG"
        ]
    except glue_client.exceptions.EntityNotFoundException:
        return []


def run_query(athena_client, database: str, sql: str) -> None:
    """Start an Athena query against `database` and block until it succeeds; raise on failure."""
    query_id = athena_client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]

    elapsed = 0
    while True:
        execution = athena_client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELLED"):
            reason = execution["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"{sql!r} {state}: {reason}")
        if elapsed >= QUERY_TIMEOUT_SECONDS:
            raise RuntimeError(
                f"{sql!r} timed out after {QUERY_TIMEOUT_SECONDS}s (query_id={query_id})"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS


def maintain(athena_client, glue_client) -> list[str]:
    """OPTIMIZE + VACUUM every Iceberg table found; returns the `database.table` names touched."""
    touched = []
    for database in DATABASES:
        for table in iceberg_tables(glue_client, database):
            qualified = f"{database}.{table}"
            run_query(athena_client, database, f"OPTIMIZE {qualified} REWRITE DATA USING BIN_PACK")
            run_query(athena_client, database, f"VACUUM {qualified}")
            touched.append(qualified)
            print(f"maintained {qualified}")
    return touched


def main() -> None:
    athena = boto3.client("athena", region_name=REGION)
    glue = boto3.client("glue", region_name=REGION)
    touched = maintain(athena, glue)
    print(f"{len(touched)} table(s) maintained" if touched else "no Iceberg tables found")


if __name__ == "__main__":
    main()
