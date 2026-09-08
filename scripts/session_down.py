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

from hyperlake.session import (
    DRAIN_SECONDS,
    estimate_cost_usd,
    latest_manifest,
    load_manifest,
    reap_marker_key,
    write_manifest,
)

REGION = "ap-northeast-1"
EPHEMERAL_DIR = Path("infra/main/ephemeral")
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
RUN_URL_RE = re.compile(r"https://\S+/actions/runs/\d+")


def append_pending_cost_row(
    csv_path: Path,
    session_id: str,
    start_iso: str,
    end_iso: str,
    cost_estimate: float,
    *,
    reaped: bool = False,
) -> None:
    row = {
        "session_id": session_id,
        "start": start_iso,
        "end": end_iso,
        "cost_estimate_usd": f"{cost_estimate:.2f}",
        "cost_actual_usd": "",
        "cost_status": "reaper-terminated" if reaped else "pending",
        "ce_query_date": "",
    }
    with csv_path.open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=COSTS_FIELDNAMES).writerow(row)


@dataclass
class SessionDownDeps:
    stop_ingester: Callable[[], None]
    destroy_ephemeral: Callable[[str, str, str], None]
    collect_gaps: Callable[[str, datetime, datetime], list[dict]]
    run_dbt: Callable[[str], dict]
    run_iceberg_maintain: Callable[[], None]
    git_commit: Callable[[list[str], str], None]
    append_cost_row: Callable[..., None]
    check_reaped: Callable[[str], dict | None]
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def run_session_down(manifest_path: Path, deps: SessionDownDeps) -> dict:
    manifest = load_manifest(manifest_path)
    for key in ("session_id", "start", "deployed_sha"):
        if key not in manifest:
            raise RuntimeError(f"malformed manifest {manifest_path}: missing '{key}'")
    session_id = manifest["session_id"]
    start = datetime.fromisoformat(manifest["start"])

    # QNT-459: a fired dead-man's switch already scaled the ingester to 0, drained, and
    # deleted the stream -- redoing the stop/wait here would just be a slow no-op, and the
    # reap must stay visible rather than looking like a plain successful session (AC6).
    # Accepted race, not an oversight: if the schedule fires *during* this run, after this
    # check returns None but before destroy_ephemeral completes, both paths independently
    # scale-to-zero/drain/touch the stream. Every effect on both sides is idempotent or
    # already tolerated (AC2's drift-tolerant destroy), so the interleaving is harmless.
    reap_marker = deps.check_reaped(session_id)
    if reap_marker is not None:
        print(
            f"session-down: WARNING -- session {session_id} was reaped at "
            f"{reap_marker['reaped_at']} (dead-man's switch fired, max_session_hours exceeded)",
            file=sys.stderr,
        )
        end_iso = reap_marker["reaped_at"]
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        manifest["reaped"] = True
        manifest["reaped_at"] = reap_marker["reaped_at"]
    else:
        deps.stop_ingester()
        deps.sleep(DRAIN_SECONDS)
        end = deps.now()
        end_iso = end.isoformat().replace("+00:00", "Z")

    gaps = deps.collect_gaps(session_id, start, end)
    # `terraform destroy` also removes the per-session reaper schedule (Terraform-managed,
    # infra/main/ephemeral/session_reaper.tf) -- no separate delete call needed, whether or
    # not it already fired.
    deps.destroy_ephemeral(manifest["deployed_sha"], session_id, manifest["start"])

    duration_hours = (end - start).total_seconds() / 3600
    cost_estimate = estimate_cost_usd(duration_hours)
    deps.append_cost_row(
        session_id, manifest["start"], end_iso, cost_estimate, reaped=reap_marker is not None
    )

    manifest["end"] = end_iso
    manifest["gaps"] = gaps
    manifest["cost_estimate_usd"] = cost_estimate

    run_key = f"session-down-{session_id}"
    dbt_result = deps.run_dbt(run_key)
    manifest["dbt_runs"] = manifest.get("dbt_runs", []) + [dbt_result]

    reaped_note = " (reaper-terminated)" if reap_marker is not None else ""
    if dbt_result["status"] != "success":
        write_manifest(manifest_path, manifest)
        deps.git_commit(
            [str(manifest_path), str(COSTS_CSV)],
            f"chore(session): record session {session_id}{reaped_note} (dbt-run failed)",
        )
        raise RuntimeError(f"session-down: dbt-run failed: {dbt_result['url']}")

    deps.run_iceberg_maintain()
    write_manifest(manifest_path, manifest)
    deps.git_commit(
        [str(manifest_path), str(COSTS_CSV)],
        f"chore(session): record session {session_id}{reaped_note}",
    )
    return manifest


def _run(*args: str) -> None:
    subprocess.run(args, check=True)


def _real_stop_ingester() -> None:
    _run("make", "ingester-stop")


def _real_destroy_ephemeral(image_tag: str, session_id: str, session_start: str) -> None:
    # None of these three vars has a default (infra/main/ephemeral/variables.tf) so all are
    # required even for destroy; the values don't have to match what's live, but Terraform
    # won't plan without them. `terraform destroy` refreshes state before planning, so a
    # stream (or, since QNT-459, the reaper schedule) already gone out-of-band is detected
    # and dropped from state rather than failing the destroy.
    _run(
        "make",
        "tf-destroy-ephemeral",
        f"TF_ARGS=-auto-approve -var image_tag={image_tag} "
        f"-var session_id={session_id} -var session_start={session_start}",
    )


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
    session_id: str, start_iso: str, end_iso: str, cost_estimate: float, *, reaped: bool = False
) -> None:
    append_pending_cost_row(COSTS_CSV, session_id, start_iso, end_iso, cost_estimate, reaped=reaped)


def _terraform_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=EPHEMERAL_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _real_check_reaped(session_id: str) -> dict | None:
    """The reap marker the reaper Lambda writes to S3 (QNT-459), if present -- there is no
    other channel from the Lambda back to this locally-run script."""
    bucket = _terraform_output("data_bucket_name")
    s3 = boto3.client("s3", region_name=REGION)
    try:
        obj = s3.get_object(Bucket=bucket, Key=reap_marker_key(session_id))
    except s3.exceptions.NoSuchKey:
        return None
    return json.loads(obj["Body"].read())


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
        check_reaped=_real_check_reaped,
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
