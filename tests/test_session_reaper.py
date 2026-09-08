"""AC4 (code): the reaper must wait >= DRAIN_SECONDS between scaling the ingester to 0 and
deleting the Kinesis stream -- the Firehose buffer's 60s window plus margin, same invariant
`session-down` observes (tests/test_session_down_drain.py). Exercised with every AWS call
stubbed and a fake clock, no real Lambda/boto3 involved.

Also pins a retry-idempotency requirement (found in review, not in the original AC list):
EventBridge Scheduler retries a failed invocation (its RetryPolicy allows up to 185 attempts
over 24h). If `put_marker` fails transiently on one attempt, a retry re-runs `reap()` from
the top -- `delete_stream` must tolerate the stream already being gone from the prior
attempt, or the retry can never reach `put_marker` and the reap-visible guarantee (AC6)
silently breaks.
"""

from datetime import UTC, datetime
from unittest import mock

from hyperlake.session import DRAIN_SECONDS, reap_marker_key
from hyperlake.session_reaper import ReaperDeps, _real_delete_stream, reap

EVENT = {
    "session_id": "dev-20260908000000",
    "cluster": "hyperlake",
    "service": "hyperlake-ingester",
    "stream_name": "hyperlake-trades",
    "bucket": "hyperlake-data-123456789012",
}


def _deps() -> tuple[ReaperDeps, list[str]]:
    calls: list[str] = []
    deps = ReaperDeps(
        scale_to_zero=lambda cluster, service: calls.append(f"scale_to_zero:{cluster}:{service}"),
        delete_stream=lambda name: calls.append(f"delete_stream:{name}"),
        put_marker=lambda bucket, key, marker: calls.append(f"put_marker:{bucket}:{key}"),
        sleep=lambda seconds: calls.append(f"sleep:{seconds}"),
        now=lambda: datetime(2026, 9, 8, 0, 6, 0, tzinfo=UTC),
    )
    return deps, calls


def test_waits_at_least_drain_seconds_between_scale_and_delete():
    deps, calls = _deps()

    reap(EVENT, deps)

    assert calls.index("scale_to_zero:hyperlake:hyperlake-ingester") < calls.index(
        f"sleep:{DRAIN_SECONDS}"
    )
    assert calls.index(f"sleep:{DRAIN_SECONDS}") < calls.index("delete_stream:hyperlake-trades")
    assert DRAIN_SECONDS >= 120


def test_writes_reap_marker_after_delete_with_reaped_at():
    deps, calls = _deps()

    marker = reap(EVENT, deps)

    assert calls.index("delete_stream:hyperlake-trades") < calls.index(
        f"put_marker:hyperlake-data-123456789012:{reap_marker_key('dev-20260908000000')}"
    )
    assert marker == {
        "session_id": "dev-20260908000000",
        "reaped": True,
        "reaped_at": "2026-09-08T00:06:00Z",
    }


class _ResourceNotFoundException(Exception):
    pass


def test_real_delete_stream_tolerates_already_deleted_stream():
    """A Scheduler retry after a prior attempt's DeleteStream already succeeded must not
    raise -- otherwise the retry can never reach put_marker (see module docstring)."""
    fake_client = mock.MagicMock()
    fake_client.exceptions.ResourceNotFoundException = _ResourceNotFoundException
    fake_client.delete_stream.side_effect = _ResourceNotFoundException("stream not found")

    with mock.patch("boto3.client", return_value=fake_client):
        _real_delete_stream("hyperlake-trades")  # must not raise
