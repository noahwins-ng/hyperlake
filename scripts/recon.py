#!/usr/bin/env python3
"""make recon SESSION=<id> (QNT-460, FR-8/ADR-003): compute the session's reconcilable
window, run recon_trades (+ its G3 tests) via the shared dbt-run workflow -- which
regenerates the session_gaps seed itself from the same committed manifest, since a
local seed write here would never reach that remote checkout -- then write the
resulting ws_only/backfill_only/both counts into the manifest's `recon` block.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import boto3

from hyperlake.recon import reconcilable_window
from hyperlake.session import load_manifest, write_manifest

REGION = "ap-northeast-1"
SESSIONS_DIR = Path("sessions")
ATHENA_SCHEMA = os.environ.get("DBT_ATHENA_SCHEMA", "silver")
ATHENA_WORKGROUP = os.environ.get("DBT_ATHENA_WORKGROUP", "hyperlake")
RUN_URL_RE = re.compile(r"https://\S+/actions/runs/\d+")
# session_up.py's LABEL_RE shape ({label}-{timestamp}, both all-lowercase/digit/dash) --
# session_id flows unquoted into the Athena query string built in _query_athena_counts.
SESSION_ID_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass
class RunReconDeps:
    run_dbt: Callable[[str, dict], dict]
    query_counts: Callable[[str], dict]
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def run_recon(manifest_path: Path, deps: RunReconDeps) -> dict:
    manifest = load_manifest(manifest_path)
    session_id = manifest["session_id"]
    if not SESSION_ID_RE.fullmatch(session_id):
        raise RuntimeError(
            f"recon: session_id {session_id!r} must match {SESSION_ID_RE.pattern} "
            "(it is interpolated into an Athena query string)"
        )
    if manifest.get("end") is None:
        raise RuntimeError(f"recon: session {session_id} has no end -- run session-down first")

    start = datetime.fromisoformat(manifest["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(manifest["end"].replace("Z", "+00:00"))
    window = reconcilable_window(start, end, deps.now())
    if window is None:
        raise RuntimeError(
            f"recon: session {session_id}: no reconcilable hour yet -- archive still lagging"
        )
    window_start, window_end = window

    # recon_trades.sql casts these with `timestamp '...'` -- Athena's TIMESTAMP literal
    # (unlike duckdb's) rejects ISO-8601 `T`/offset formatting, so use the plain SQL
    # literal shape both targets accept (window_start/end are already UTC, ADR-003).
    dbt_vars = {
        "recon_session_id": session_id,
        "recon_window_start": window_start.strftime("%Y-%m-%d %H:%M:%S"),
        "recon_window_end": window_end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # A bare `recon-{session_id}` run_key collides across repeated invocations for the
    # same session (e.g. a retry after a transient failure, or `make heal` calling
    # this a second time) -- gh_run.sh locates a run by exact displayTitle match, so a
    # reused key can watch a *stale* run instead of the one just dispatched (found
    # 2026-09-10 running QNT-461's heal verification against a retried recon). Stamp
    # it with `now` like `scripts/heal.py`'s own run_key already does.
    run_key = f"recon-{session_id}-{int(deps.now().timestamp())}"
    dbt_result = deps.run_dbt(run_key, dbt_vars)
    if dbt_result["status"] != "success":
        raise RuntimeError(f"recon: dbt-run failed: {dbt_result['url']}")

    counts = deps.query_counts(session_id)
    manifest["recon"] = {
        "ws_only": counts["ws_only"],
        "backfill_only": counts["backfill_only"],
        "both": counts["both"],
        "window": f"{window_start.isoformat()}/{window_end.isoformat()}",
    }
    write_manifest(manifest_path, manifest)
    return manifest


def _real_run_dbt(run_key: str, dbt_vars: dict) -> dict:
    result = subprocess.run(
        [
            "scripts/gh_run.sh",
            run_key,
            "-f",
            "select=tag:recon",
            "-f",
            f"vars={json.dumps(dbt_vars)}",
        ],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    match = RUN_URL_RE.search(output)
    url = match.group(0) if match else ""
    status = "success" if result.returncode == 0 else "failed"
    print(output, file=sys.stderr if status == "failed" else sys.stdout)
    return {"run_key": run_key, "url": url, "status": status}


def _query_athena_counts(athena_client, session_id: str) -> dict:
    """Not type-annotated on `athena_client` (matches scripts/iceberg_maintain.py's
    `run_query`): boto3-stubs marks several response fields NotRequired even though
    AWS always returns them for a completed query, which would otherwise fail pyright."""
    qid = athena_client.start_query_execution(
        QueryString=(
            f"select membership, count(*) as n from {ATHENA_SCHEMA}.recon_trades "
            f"where session_id = '{session_id}' group by membership"
        ),
        QueryExecutionContext={"Database": ATHENA_SCHEMA},
        WorkGroup=ATHENA_WORKGROUP,
    )["QueryExecutionId"]

    while True:
        execution = athena_client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = execution["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"recon: Athena query {qid} {state}")
        time.sleep(1)

    counts = {"ws_only": 0, "backfill_only": 0, "both": 0}
    rows = athena_client.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"][1:]
    for row in rows:
        membership, n = (d["VarCharValue"] for d in row["Data"])
        counts[membership] = int(n)
    return counts


def _real_query_counts(session_id: str) -> dict:
    athena = boto3.client("athena", region_name=REGION)
    return _query_athena_counts(athena, session_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="session_id (sessions/<id>.json)")
    args = parser.parse_args()

    manifest_path = SESSIONS_DIR / f"{args.session}.json"
    if not manifest_path.exists():
        print(f"recon: no manifest at {manifest_path}", file=sys.stderr)
        raise SystemExit(1)

    deps = RunReconDeps(
        run_dbt=_real_run_dbt,
        query_counts=_real_query_counts,
    )
    try:
        manifest = run_recon(manifest_path, deps)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1) from e

    print(f"recon: {manifest['session_id']} -- {json.dumps(manifest['recon'])}")


if __name__ == "__main__":
    main()
