"""Session lifecycle helpers for `make session-up` / `make session-down` (QNT-458, FR-8):
cost estimation and the manifest-state guard that keeps overlapping sessions from
outrunning the reaper's 6h bound.
"""

import json
from pathlib import Path

ECR_REPOSITORY = "hyperlake-ingester"

# ap-northeast-1 list prices, docs/prd.md S8 ("Cost model"). Fargate ingester is
# 0.25 vCPU / 0.5 GB; Kinesis and Firehose are on-demand.
FARGATE_HOURLY_USD = 0.016
KINESIS_HOURLY_USD = 0.048
KINESIS_PER_GB_USD = 0.10
FIREHOSE_INGEST_PER_GB_USD = 0.036
FIREHOSE_CONVERSION_PER_GB_USD = 0.022
FIREHOSE_PARTITION_PER_GB_USD = 0.024
FIREHOSE_PARTITION_PER_1K_OBJECTS_USD = 0.006
# `iceberg-maintain`'s OPTIMIZE + VACUUM scan silver end to end regardless of session
# length; PRD S8 measures silver at ~1.5 GB for a 30-day backfill, at Athena's $5/TB.
ATHENA_MAINTENANCE_USD = 0.0075
# CloudWatch Logs ingestion (ingester + Firehose delivery logs), S3 PUT/GET request
# charges, and AWS's per-resource minimum billing granularity -- not broken out as
# separate line items in the PRD's cost table, but consistently present in a live
# session. Sized so a 4h reference session lands inside the PRD's own headline
# "Session total ~= $0.50-1" (docs/prd.md S8), rather than the ~$0.30 the itemized
# rows alone sum to.
OVERHEAD_HOURLY_USD = 0.075

# Measured throughput (docs/prd.md S8, 2026-09-04 spike): a 4h session streams
# ~185k trades ~= 55 MB raw. Used to scale Kinesis/Firehose GB-based charges by duration.
BYTES_PER_SECOND = 55_000_000 / (4 * 3600)
# Firehose's dynamic-partitioning buffer hint (infra/main/ephemeral/kinesis_firehose.tf):
# 64 MB / 60s -- used only to approximate object count for the $/1k-objects charge.
FIREHOSE_AVG_OBJECT_BYTES = 64_000_000

# >= one Firehose buffer window (60s, infra/main/ephemeral/kinesis_firehose.tf) with margin,
# so records already in flight when the ingester stops still land in S3 before the stream
# that carries them is destroyed. Shared by `session-down` and the session reaper
# (QNT-459) -- both scale the ingester to 0 then wait this long before touching the stream.
DRAIN_SECONDS = 120


def reap_marker_key(session_id: str) -> str:
    """S3 key (under the data bucket's `sessions/` prefix) the reaper Lambda writes to
    signal a fired dead-man's switch -- the Lambda has no git/repo access, so this is how
    `session-down` (own ticket, QNT-458) learns a session was reaped out-of-band."""
    return f"sessions/{session_id}.reaped.json"


def estimate_cost_usd(duration_hours: float) -> float:
    """Approximate a demo session's cost from resource-hours x list price."""
    raw_gb = BYTES_PER_SECOND * duration_hours * 3600 / 1e9
    firehose_objects = raw_gb * 1e9 / FIREHOSE_AVG_OBJECT_BYTES

    fargate = FARGATE_HOURLY_USD * duration_hours
    kinesis = KINESIS_HOURLY_USD * duration_hours + KINESIS_PER_GB_USD * raw_gb
    firehose = (FIREHOSE_INGEST_PER_GB_USD + FIREHOSE_CONVERSION_PER_GB_USD) * raw_gb
    partitioning = (
        FIREHOSE_PARTITION_PER_GB_USD * raw_gb
        + FIREHOSE_PARTITION_PER_1K_OBJECTS_USD * (firehose_objects / 1000)
    )
    overhead = OVERHEAD_HOURLY_USD * duration_hours

    return round(fargate + kinesis + firehose + partitioning + ATHENA_MAINTENANCE_USD + overhead, 2)


def latest_manifest(sessions_dir: Path) -> Path | None:
    """The most recent `sessions/<session_id>.json` manifest by its recorded `start` time
    (not filename order -- session_id's label prefix varies, so filenames with different
    labels don't sort chronologically), or None if none exist yet."""
    manifests = list(sessions_dir.glob("*.json"))
    if not manifests:
        return None
    return max(manifests, key=lambda p: load_manifest(p)["start"])


def session_is_open(manifest: dict | None) -> bool:
    """True if `manifest` describes a session `session-up` must refuse to start over:
    no prior session (None) never blocks; a session is open only while it has no `end`
    and hasn't been force-closed by the reaper (`reaped: true`)."""
    if manifest is None:
        return False
    return manifest.get("end") is None and not manifest.get("reaped", False)


def load_manifest(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def write_manifest(path: Path, manifest: dict) -> None:
    with path.open("w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def latest_image_tag(ecr_client) -> str:
    """The commit SHA of the most recently pushed ingester image
    (.github/workflows/ingester-image.yml tags each push with its commit SHA) --
    not `git rev-parse HEAD`, since that workflow only fires on ingester-relevant path
    changes and HEAD can advance past the last commit that actually built an image.
    Shared by `session-up` (a live session's ingester) and `heal` (re-applying just the
    backfill sub-stack, QNT-461) -- both need whatever image is already in ECR, not a
    new build of their own."""
    images = ecr_client.describe_images(repositoryName=ECR_REPOSITORY)["imageDetails"]
    tagged = [i for i in images if i.get("imageTags")]
    newest = max(tagged, key=lambda i: i["imagePushedAt"])
    return newest["imageTags"][0]
