#!/usr/bin/env python3
"""make heal SESSION=<id> (QNT-461, FR-8): expand every unhealed gap in a session's
manifest to its covering archive hour files (boundary rule, hyperlake.heal.gap_to_hours),
abort loud (no execution started) if any covering hour's archive file hasn't landed yet,
otherwise re-run the backfill Map over exactly those hours, re-run dbt-run with
silver_lookback_days sized to the oldest unhealed gap (hyperlake.heal.lookback_days),
re-run recon, flip every healed gap `healed: true`, and commit the manifest. Backfill's
deterministic per-hour object keys and dbt's merge-on-tid make every step safe to
re-run (AC4) -- a second `make heal` on an already-healed session finds nothing unhealed
and no-ops before touching AWS at all.
"""

import argparse
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
from botocore.exceptions import ClientError

from hyperlake.backfill.official import ARCHIVE_BUCKET, ARCHIVE_PREFIX
from hyperlake.heal import gap_to_hours, lookback_days
from hyperlake.session import latest_image_tag, load_manifest, write_manifest

REGION = "ap-northeast-1"
SESSIONS_DIR = Path("sessions")
EPHEMERAL_DIR = Path("infra/main/ephemeral")
POLL_INTERVAL_SECONDS = 5
RUN_URL_RE = re.compile(r"https://\S+/actions/runs/\d+")
# manifest session_id flows unquoted into TF_ARGS below (the Makefile expands that into a
# shell-executed terraform command, same concern session_up.py's LABEL_RE documents) --
# reject anything outside the charset Terraform/AWS resource names already require.
SESSION_ID_RE = re.compile(r"^[a-z0-9-]+$")


def _dedupe_hours(gaps_hours: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    seen = {(h["date"], int(h["hour"])): h for hours in gaps_hours for h in hours}
    return [seen[k] for k in sorted(seen)]


@dataclass
class HealDeps:
    check_landed: Callable[[dict], bool]
    # session_id is passed explicitly (not closed over from an outer scope) so the only
    # value that can reach a shell-interpolated terraform command is the one run_heal
    # already validated against SESSION_ID_RE below -- an unvalidated CLI arg closed
    # over separately would bypass that check entirely (found in review).
    start_backfill: Callable[[list[dict], str], dict]
    run_dbt: Callable[[str, dict], dict]
    run_recon: Callable[[str], None]
    git_commit: Callable[[list[str], str], None]
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def run_heal(manifest_path: Path, deps: HealDeps) -> dict:
    manifest = load_manifest(manifest_path)
    session_id = manifest["session_id"]
    if not SESSION_ID_RE.fullmatch(session_id):
        raise RuntimeError(
            f"heal: session_id {session_id!r} must match {SESSION_ID_RE.pattern} "
            "(it is interpolated into a shell-executed terraform command)"
        )
    gaps = manifest.get("gaps", [])
    unhealed = [g for g in gaps if not g.get("healed", False)]

    if not unhealed:
        print(f"heal: {session_id} -- no unhealed gaps, nothing to do")
        return manifest

    def _parse(iso: str) -> datetime:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    try:
        hours = _dedupe_hours(
            [gap_to_hours(_parse(g["start"]), _parse(g["end"])) for g in unhealed]
        )
    except ValueError as e:
        raise RuntimeError(f"heal: {session_id} -- malformed gap: {e}") from e

    not_landed = [h for h in hours if not deps.check_landed(h)]
    if not_landed:
        rendered = ", ".join(f"{h['date']}/{h['hour']}" for h in not_landed)
        raise RuntimeError(
            f"heal: {session_id} -- not yet landed: {rendered} (archive lags ~1h; re-run later)"
        )

    deps.start_backfill(hours, session_id)

    now = deps.now()
    n_lookback = lookback_days(gaps, now.date())
    run_key = f"heal-{session_id}-{int(now.timestamp())}"
    # QNT-466 (2026-09-11 live session): without these, assert_silver_freshness checks
    # against dbt_project.yml's placeholder demo-fixture window instead of this session's
    # real one and fails regardless of actual freshness (same fix as session_down.py's
    # run_dbt call) -- plain SQL literal shape, dbt's TIMESTAMP rejects ISO-8601 `T`/offset.
    dbt_vars = {
        "silver_lookback_days": n_lookback,
        "freshness_window_start": _parse(manifest["start"]).strftime("%Y-%m-%d %H:%M:%S"),
        "freshness_window_end": _parse(manifest["end"]).strftime("%Y-%m-%d %H:%M:%S"),
    }
    dbt_result = deps.run_dbt(run_key, dbt_vars)
    manifest["dbt_runs"] = manifest.get("dbt_runs", []) + [dbt_result]

    if dbt_result["status"] != "success":
        write_manifest(manifest_path, manifest)
        deps.git_commit(
            [str(manifest_path)], f"chore(session): heal {session_id} attempt (dbt-run failed)"
        )
        raise RuntimeError(f"heal: dbt-run failed: {dbt_result['url']}")

    try:
        deps.run_recon(session_id)
    except Exception:
        # dbt_result is already recorded above -- persist that much of the attempt
        # (matching the dbt-failure branch just above) rather than silently dropping
        # it just because the next step failed too.
        write_manifest(manifest_path, manifest)
        deps.git_commit(
            [str(manifest_path)], f"chore(session): heal {session_id} attempt (recon failed)"
        )
        raise

    for gap in gaps:
        if not gap.get("healed", False):
            gap["healed"] = True
    write_manifest(manifest_path, manifest)
    deps.git_commit([str(manifest_path)], f"chore(session): heal {session_id}")
    return manifest


def _real_check_landed(s3, hour: dict) -> bool:
    try:
        s3.head_object(
            Bucket=ARCHIVE_BUCKET,
            Key=f"{ARCHIVE_PREFIX}/{hour['date']}/{hour['hour']}.lz4",
            RequestPayer="requester",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "404":
            return False
        raise
    return True


def _terraform_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", name], cwd=EPHEMERAL_DIR, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"heal: terraform output {name} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _tf_ephemeral_vars(image_tag: str, session_id: str, session_start: str) -> str:
    return (
        f"-auto-approve -var image_tag={image_tag} "
        f"-var session_id={session_id} -var session_start={session_start}"
    )


def _real_apply_ephemeral(image_tag: str, session_id: str, session_start: str) -> None:
    result = subprocess.run(
        [
            "make",
            "tf-apply-ephemeral",
            f"TF_ARGS={_tf_ephemeral_vars(image_tag, session_id, session_start)}",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError("heal: tf-apply-ephemeral failed")


def _real_destroy_ephemeral(image_tag: str, session_id: str, session_start: str) -> None:
    result = subprocess.run(
        [
            "make",
            "tf-destroy-ephemeral",
            f"TF_ARGS={_tf_ephemeral_vars(image_tag, session_id, session_start)}",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError("heal: tf-destroy-ephemeral failed")


def _run_backfill_execution(sfn_client, state_machine_arn: str, hours: list[dict]) -> dict:
    name = f"heal-{int(time.time())}"
    start = sfn_client.start_execution(
        stateMachineArn=state_machine_arn,
        name=name,
        input=json.dumps({"hours": hours}),
    )
    while True:
        desc = sfn_client.describe_execution(executionArn=start["executionArn"])
        if desc["status"] != "RUNNING":
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    if desc["status"] != "SUCCEEDED":
        raise RuntimeError(f"heal: backfill execution {desc['status']}: {desc.get('cause', '')}")
    output = json.loads(desc["output"])
    failed = [r for r in output.get("results", []) if r.get("status") == "failed"]
    if failed:
        raise RuntimeError(f"heal: backfill failed hours: {failed}")
    return output


def _real_start_backfill(hours: list[dict], session_id: str) -> dict:
    """Backfill's Lambda + Step Functions state machine live in the ephemeral Terraform
    stack, which `session-down` already destroyed by the time a session has any
    recorded gap to heal (gaps are only ever written by session-down itself, after
    `destroy_ephemeral`) -- so healing must re-apply that stack just for this
    execution and tear it back down after, rather than assume it's still up (found in
    review: an earlier version read `backfill_state_machine_arn` eagerly in `main()`,
    which crashed on every realistic invocation)."""
    ecr = boto3.client("ecr", region_name=REGION)
    image_tag = latest_image_tag(ecr)
    heal_session_id = f"heal-{session_id}-{int(time.time())}"
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        _real_apply_ephemeral(image_tag, heal_session_id, now_iso)
        sfn = boto3.client("stepfunctions", region_name=REGION)
        state_machine_arn = _terraform_output("backfill_state_machine_arn")
        return _run_backfill_execution(sfn, state_machine_arn, hours)
    finally:
        _real_destroy_ephemeral(image_tag, heal_session_id, now_iso)


def _real_run_dbt(run_key: str, dbt_vars: dict) -> dict:
    result = subprocess.run(
        ["scripts/gh_run.sh", run_key, "-f", f"vars={json.dumps(dbt_vars)}"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    match = RUN_URL_RE.search(output)
    url = match.group(0) if match else ""
    status = "success" if result.returncode == 0 else "failed"
    print(output, file=sys.stderr if status == "failed" else sys.stdout)
    return {"run_key": run_key, "url": url, "status": status}


def _real_run_recon(session_id: str) -> None:
    result = subprocess.run(["make", "recon", f"SESSION={session_id}"])
    if result.returncode != 0:
        raise RuntimeError(f"heal: recon failed for session {session_id}")


def _real_git_commit(paths: list[str], message: str) -> None:
    subprocess.run(["git", "add", *paths], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="session_id (sessions/<id>.json)")
    args = parser.parse_args()

    manifest_path = SESSIONS_DIR / f"{args.session}.json"
    if not manifest_path.exists():
        print(f"heal: no manifest at {manifest_path}", file=sys.stderr)
        raise SystemExit(1)

    s3 = boto3.client("s3", region_name=REGION)

    deps = HealDeps(
        check_landed=lambda hour: _real_check_landed(s3, hour),
        start_backfill=_real_start_backfill,
        run_dbt=_real_run_dbt,
        run_recon=_real_run_recon,
        git_commit=_real_git_commit,
    )
    try:
        manifest = run_heal(manifest_path, deps)
    except RuntimeError as e:
        print(f"heal: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    print(f"heal: {manifest['session_id']} done")


if __name__ == "__main__":
    main()
