#!/usr/bin/env python3
"""session-down (QNT-458, FR-8): stop the ingester, drain the Firehose buffer, destroy
the ephemeral stack, collect the session's gap log, record cost_estimate + a pending
costs row, trigger dbt-run then iceberg-maintain, finalize the manifest, and commit it.
A failing dbt-run still finalizes and commits the manifest (with the failed run's URL)
but skips iceberg-maintain and exits non-zero (AC5) -- the ephemeral stack is already
torn down by that point, so nothing billable is left running either way.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import boto3

from hyperlake.session import estimate_cost_usd, latest_manifest, load_manifest, write_manifest

REGION = "ap-northeast-1"
SESSIONS_DIR = Path("sessions")
COSTS_CSV = Path("costs/sessions.csv")
COSTS_FIELDNAMES = [
    "session_id",
    "start",
    "end",
    "cost_estimate_usd",
    "cost_actual_usd",
    "cost_status",
    "ce_query_date",
]
LOG_GROUP = "/ecs/hyperlake-ingester"
# >= one Firehose buffer window (60s, infra/main/ephemeral/kinesis_firehose.tf) with margin,
# so records already in flight when the ingester stops still land in S3 before the stream
# that carries them is destroyed.
DRAIN_SECONDS = 120
RUN_URL_RE = re.compile(r"https://\S+/actions/runs/\d+")


def append_pending_cost_row(
    csv_path: Path, session_id: str, start_iso: str, end_iso: str, cost_estimate: float
) -> None:
    row = {
        "session_id": session_id,
        "start": start_iso,
        "end": end_iso,
        "cost_estimate_usd": f"{cost_estimate:.2f}",
        "cost_actual_usd": "",
        "cost_status": "pending",
        "ce_query_date": "",
    }
    with csv_path.open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=COSTS_FIELDNAMES).writerow(row)


@dataclass
class SessionDownDeps:
    stop_ingester: Callable[[], None]
    destroy_ephemeral: Callable[[str], None]
    collect_gaps: Callable[[str, datetime, datetime], list[dict]]
    run_dbt: Callable[[str], dict]
    run_iceberg_maintain: Callable[[], None]
    git_commit: Callable[[list[str], str], None]
    append_cost_row: Callable[[str, str, str, float], None]
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def run_session_down(manifest_path: Path, deps: SessionDownDeps) -> dict:
    manifest = load_manifest(manifest_path)
    for key in ("session_id", "start", "deployed_sha"):
        if key not in manifest:
            raise RuntimeError(f"malformed manifest {manifest_path}: missing '{key}'")
    start = datetime.fromisoformat(manifest["start"])

    deps.stop_ingester()
    deps.sleep(DRAIN_SECONDS)
    end = deps.now()
    gaps = deps.collect_gaps(manifest["session_id"], start, end)
    deps.destroy_ephemeral(manifest["deployed_sha"])

    duration_hours = (end - start).total_seconds() / 3600
    cost_estimate = estimate_cost_usd(duration_hours)
    end_iso = end.isoformat().replace("+00:00", "Z")
    deps.append_cost_row(manifest["session_id"], manifest["start"], end_iso, cost_estimate)

    manifest["end"] = end_iso
    manifest["gaps"] = gaps
    manifest["cost_estimate_usd"] = cost_estimate

    run_key = f"session-down-{manifest['session_id']}"
    dbt_result = deps.run_dbt(run_key)
    manifest["dbt_runs"] = manifest.get("dbt_runs", []) + [dbt_result]

    if dbt_result["status"] != "success":
        write_manifest(manifest_path, manifest)
        deps.git_commit(
            [str(manifest_path), str(COSTS_CSV)],
            f"chore(session): record session {manifest['session_id']} (dbt-run failed)",
        )
        raise RuntimeError(f"session-down: dbt-run failed: {dbt_result['url']}")

    deps.run_iceberg_maintain()
    write_manifest(manifest_path, manifest)
    deps.git_commit(
        [str(manifest_path), str(COSTS_CSV)],
        f"chore(session): record session {manifest['session_id']}",
    )
    return manifest


def _run(*args: str) -> None:
    subprocess.run(args, check=True)


def _real_stop_ingester() -> None:
    _run("make", "ingester-stop")


def _real_destroy_ephemeral(image_tag: str) -> None:
    # `image_tag` has no default (infra/main/ephemeral/variables.tf) so it's required even
    # for destroy; the value doesn't have to match what's live, but Terraform won't plan
    # without one. `terraform destroy` refreshes state before planning, so a stream already
    # gone out-of-band is detected and dropped from state rather than failing the destroy.
    _run("make", "tf-destroy-ephemeral", f"TF_ARGS=-auto-approve -var image_tag={image_tag}")


def _real_collect_gaps(session_id: str, start: datetime, end: datetime) -> list[dict]:
    """`gap_recorded` events (hyperlake.ingester.log_event) the ingester logged to
    CloudWatch Logs during `[start, end]`, as manifest-shaped `{start, end, healed}`
    entries (`healed` is filled later, by the `heal` ticket)."""
    logs = boto3.client("logs", region_name=REGION)
    events = []
    kwargs = {
        "logGroupName": LOG_GROUP,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "filterPattern": '{ $.event = "gap_recorded" }',
    }
    while True:
        resp = logs.filter_log_events(**kwargs)
        events.extend(resp["events"])
        token = resp.get("nextToken")
        if not token:
            break
        kwargs["nextToken"] = token

    gaps = []
    for e in events:
        gap = json.loads(e["message"])
        gaps.append(
            {
                "start": datetime.fromtimestamp(gap["gap_start"] / 1000, tz=UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "end": datetime.fromtimestamp(gap["gap_end"] / 1000, tz=UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "healed": False,
            }
        )
    return gaps


def _real_run_dbt(run_key: str, extra_args: list[str] | None = None) -> dict:
    result = subprocess.run(
        ["scripts/gh_run.sh", run_key, *(extra_args or [])], capture_output=True, text=True
    )
    output = result.stdout + result.stderr
    match = RUN_URL_RE.search(output)
    url = match.group(0) if match else ""
    status = "success" if result.returncode == 0 else "failed"
    print(output, file=sys.stderr if status == "failed" else sys.stdout)
    return {"run_key": run_key, "url": url, "status": status}


def _real_run_iceberg_maintain() -> None:
    _run("make", "iceberg-maintain")


def _real_git_commit(paths: list[str], message: str) -> None:
    _run("git", "add", *paths)
    _run("git", "commit", "-m", message)


def _real_append_cost_row(
    session_id: str, start_iso: str, end_iso: str, cost_estimate: float
) -> None:
    append_pending_cost_row(COSTS_CSV, session_id, start_iso, end_iso, cost_estimate)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dbt-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="extra scripts/gh_run.sh inputs, e.g. -f select=nonexistent_model to force a "
        "failure (AC5 verification); normal use passes none, for the default full build",
    )
    args = parser.parse_args()

    manifest_path = latest_manifest(SESSIONS_DIR)
    if manifest_path is None:
        print("session-down: no session manifest found in sessions/", file=sys.stderr)
        raise SystemExit(1)

    deps = SessionDownDeps(
        stop_ingester=_real_stop_ingester,
        destroy_ephemeral=_real_destroy_ephemeral,
        collect_gaps=_real_collect_gaps,
        run_dbt=lambda run_key: _real_run_dbt(run_key, extra_args=args.dbt_args),
        run_iceberg_maintain=_real_run_iceberg_maintain,
        git_commit=_real_git_commit,
        append_cost_row=_real_append_cost_row,
    )
    try:
        manifest = run_session_down(manifest_path, deps)
    except RuntimeError as e:
        print(f"session-down: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    print(
        f"session-down: {manifest['session_id']} closed, cost_estimate_usd="
        f"{manifest['cost_estimate_usd']}"
    )


if __name__ == "__main__":
    main()
