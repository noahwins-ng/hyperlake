#!/usr/bin/env python3
"""session-up (QNT-458, FR-8): refuse to start over an unfinished/forgotten prior session,
apply the ephemeral Terraform stack, start the ingester, and write the manifest stub.
"""

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3

from hyperlake.session import latest_manifest, load_manifest, session_is_open, write_manifest
from hyperlake.watchlist import load_watchlist

REGION = "ap-northeast-1"
ECR_REPOSITORY = "hyperlake-ingester"
EPHEMERAL_DIR = Path("infra/main/ephemeral")
SESSIONS_DIR = Path("sessions")
KINESIS_STREAM_ADDRESS = "aws_kinesis_stream.trades"


def stream_exists_in_state(state_list_output: str) -> bool:
    return KINESIS_STREAM_ADDRESS in state_list_output.splitlines()


def preflight_blocker(manifest: dict | None, state_list_output: str) -> str | None:
    """Reason `session-up` must refuse to start, or None if clear -- checked before any
    `terraform apply` so an overlapping/forgotten session never outruns the reaper's 6h
    bound (PRD FR-8)."""
    if manifest is not None and session_is_open(manifest):
        return f"unfinished session {manifest['session_id']} (no end / not reaped)"
    if stream_exists_in_state(state_list_output):
        return "ephemeral Kinesis stream already exists in Terraform state"
    return None


def _terraform_state_list(cwd: Path) -> str:
    subprocess.run(
        ["terraform", "init", "-backend-config=../backend.hcl", "-input=false"],
        cwd=cwd,
        capture_output=True,
        check=True,
    )
    result = subprocess.run(
        ["terraform", "state", "list"], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout


def latest_image_tag(ecr_client) -> str:
    """The commit SHA of the most recently pushed ingester image
    (.github/workflows/ingester-image.yml tags each push with its commit SHA) --
    not `git rev-parse HEAD`, since that workflow only fires on ingester-relevant path
    changes and HEAD can advance past the last commit that actually built an image."""
    images = ecr_client.describe_images(repositoryName=ECR_REPOSITORY)["imageDetails"]
    tagged = [i for i in images if i.get("imageTags")]
    newest = max(tagged, key=lambda i: i["imagePushedAt"])
    return newest["imageTags"][0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="dev", help="session_id prefix (default: dev)")
    args = parser.parse_args()

    manifest_path = latest_manifest(SESSIONS_DIR)
    manifest = load_manifest(manifest_path) if manifest_path else None
    blocker = preflight_blocker(manifest, _terraform_state_list(EPHEMERAL_DIR))
    if blocker:
        print(f"session-up: refusing to start -- {blocker}", file=sys.stderr)
        raise SystemExit(1)

    image_tag = latest_image_tag(boto3.client("ecr", region_name=REGION))

    subprocess.run(
        ["make", "tf-apply-ephemeral", f"TF_ARGS=-auto-approve -var image_tag={image_tag}"],
        check=True,
    )
    subprocess.run(["make", "ingester-start"], check=True)

    SESSIONS_DIR.mkdir(exist_ok=True)
    start = datetime.now(UTC)
    session_id = f"{args.label}-{start.strftime('%Y%m%d%H%M%S')}"
    manifest = {
        "session_id": session_id,
        "coins": load_watchlist(),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": None,
        "deployed_sha": image_tag,
        "gaps": [],
        "recon": {"ws_only": None, "backfill_only": None, "both": None, "window": None},
        "dbt_runs": [],
        "cost_estimate_usd": None,
        "cost_actual_usd": None,
        "reaped": False,
    }
    write_manifest(SESSIONS_DIR / f"{session_id}.json", manifest)
    print(session_id)


if __name__ == "__main__":
    main()
