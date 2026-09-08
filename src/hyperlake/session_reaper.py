"""Session reaper Lambda (QNT-459, FR-8 dead-man's switch): tears down a session left
running past `max_session_hours`. Invoked once by the per-session EventBridge Scheduler
`at()` schedule (`aws_scheduler_schedule.session_reaper`, infra/main/ephemeral/
session_reaper.tf) that every `session-up` apply creates and every `session-down` destroy
tears down (AC5). Scales the ingester to 0, waits for the Firehose buffer to drain, deletes
the Kinesis stream, then writes a reap marker to S3 -- `session-down`
(scripts/session_down.py) reads that marker to detect the reap and record it loudly instead
of looking like a plain successful session (AC6). Has no git/repo access, so the marker --
not the locally-committed manifest -- is the only way it can signal what happened.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hyperlake.session import DRAIN_SECONDS, reap_marker_key

REGION = "ap-northeast-1"


@dataclass
class ReaperDeps:
    scale_to_zero: Callable[[str, str], None]
    delete_stream: Callable[[str], None]
    put_marker: Callable[[str, str, dict], None]
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def reap(event: dict, deps: ReaperDeps) -> dict:
    """Run one reap: scale-to-zero -> drain wait -> delete stream -> write marker (AC4's
    ordering). `event` is the Scheduler target input built by `session-up`: session_id,
    cluster, service, stream_name, bucket."""
    deps.scale_to_zero(event["cluster"], event["service"])
    deps.sleep(DRAIN_SECONDS)
    deps.delete_stream(event["stream_name"])

    marker = {
        "session_id": event["session_id"],
        "reaped": True,
        "reaped_at": deps.now().isoformat().replace("+00:00", "Z"),
    }
    deps.put_marker(event["bucket"], reap_marker_key(event["session_id"]), marker)
    return marker


def _real_scale_to_zero(cluster: str, service: str) -> None:
    import boto3

    boto3.client("ecs", region_name=REGION).update_service(
        cluster=cluster, service=service, desiredCount=0
    )


def _real_delete_stream(stream_name: str) -> None:
    """Tolerates the stream already being gone -- a Scheduler retry (its RetryPolicy allows
    up to 185 attempts) re-runs `reap()` from the top, and a prior attempt may have deleted
    the stream successfully before failing later (e.g. a transient `put_marker` error).
    Without this, that retry could never reach `put_marker` (module docstring)."""
    import boto3

    client = boto3.client("kinesis", region_name=REGION)
    try:
        client.delete_stream(StreamName=stream_name)
    except client.exceptions.ResourceNotFoundException:
        pass


def _real_put_marker(bucket: str, key: str, marker: dict) -> None:
    import boto3

    boto3.client("s3", region_name=REGION).put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(marker).encode(),
        ContentType="application/json",
    )


def handler(event: dict, context) -> dict:
    deps = ReaperDeps(
        scale_to_zero=_real_scale_to_zero,
        delete_stream=_real_delete_stream,
        put_marker=_real_put_marker,
    )
    return reap(event, deps)
