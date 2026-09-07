#!/usr/bin/env python3
"""Compare `infra/main/persistent`'s Terraform state against the live Glue catalog and fail
loudly on drift in either direction (QNT-474): a live database/table Terraform doesn't know
about (hand-created out-of-band, e.g. QNT-473's `silver` incident), or a state entry with no
live counterpart. Detection only -- no auto-remediation.
"""

import json
import subprocess

import boto3
from botocore.exceptions import ClientError

PERSISTENT_DIR = "infra/main/persistent"
REGION = "ap-northeast-1"
STATE_PULL_TIMEOUT_SECONDS = 60  # a stalled S3 backend call fails loud instead of hanging

# Athena/Glue auto-creates a "default" database in every account/region the first time
# Athena is used there -- an AWS platform artifact, not a project resource, and never
# something Terraform could own. Reporting it would be permanent, unfixable noise.
IGNORED_DATABASES = frozenset({"default"})

# Expected when listing tables in a database this role can't fully see (deleted between
# GetDatabases and GetTables, or a database outside the OIDC role's Glue table scope --
# e.g. a hand-created rogue database). Any other Glue error (throttling, internal error,
# ...) propagates and fails the check loudly rather than being silently treated as clean.
_SKIPPABLE_TABLE_ERROR_CODES = frozenset({"EntityNotFoundException", "AccessDeniedException"})


def terraform_state(cwd: str) -> dict:
    """The persistent stack's state as the raw `terraform state pull` JSON document."""
    result = subprocess.run(
        ["terraform", "state", "pull"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=STATE_PULL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"terraform state pull failed: {result.stderr}")
    return json.loads(result.stdout)


def state_glue_resources(state: dict) -> tuple[set[str], set[tuple[str, str]]]:
    """Glue database names and (database, table) pairs declared in `state`."""
    databases: set[str] = set()
    tables: set[tuple[str, str]] = set()
    for resource in state.get("resources", []):
        for instance in resource.get("instances", []):
            attrs = instance.get("attributes", {})
            if resource["type"] == "aws_glue_catalog_database":
                databases.add(attrs["name"])
            elif resource["type"] == "aws_glue_catalog_table":
                tables.add((attrs["database_name"], attrs["name"]))
    return databases, tables


def live_glue_resources(
    glue_client, tf_tables: set[tuple[str, str]]
) -> tuple[set[str], set[tuple[str, str]]]:
    """Every live Glue database (minus `IGNORED_DATABASES`), and (database, table) pairs --
    table enumeration is scoped to databases Terraform actually declares tables in (per
    `tf_tables`, currently just `bronze`), since silver/gold tables are legitimately owned by
    dbt-athena, not Terraform (CLAUDE.md: "Iceberg exists only at silver/gold and is written
    exclusively by dbt-athena"). By design, this means a hand-created table *inside* an
    already-tracked silver/gold database is invisible here -- only a whole rogue database is
    caught in that case (via the database-level diff in `check()`); table-level drift
    detection is bronze-only. A database this role can't list tables in is skipped rather
    than crashing the whole check -- its presence is still caught at the database level."""
    live_databases = {
        d["Name"]
        for page in glue_client.get_paginator("get_databases").paginate()
        for d in page["DatabaseList"]
    } - IGNORED_DATABASES

    tables: set[tuple[str, str]] = set()
    for database in {d for d, _ in tf_tables}:
        try:
            for page in glue_client.get_paginator("get_tables").paginate(DatabaseName=database):
                tables.update((database, t["Name"]) for t in page["TableList"])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in _SKIPPABLE_TABLE_ERROR_CODES:
                raise
            continue

    return live_databases, tables


def check(state: dict, glue_client) -> dict[str, list[str]]:
    """The four drift categories, each a sorted list of `name` (databases) or `db.table`
    (tables); an empty check means state and live agree."""
    tf_databases, tf_tables = state_glue_resources(state)
    live_databases, live_tables = live_glue_resources(glue_client, tf_tables)

    return {
        "hand_created_databases": sorted(live_databases - tf_databases),
        "orphan_state_databases": sorted(tf_databases - live_databases),
        "hand_created_tables": sorted(f"{d}.{t}" for d, t in live_tables - tf_tables),
        "orphan_state_tables": sorted(f"{d}.{t}" for d, t in tf_tables - live_tables),
    }


def main() -> None:
    findings = check(terraform_state(PERSISTENT_DIR), boto3.client("glue", region_name=REGION))

    drifted = False
    for label, names in findings.items():
        for name in names:
            drifted = True
            print(f"DRIFT [{label}]: {name}")

    if drifted:
        raise SystemExit(1)
    print("no drift: terraform state matches live Glue catalog")


if __name__ == "__main__":
    main()
