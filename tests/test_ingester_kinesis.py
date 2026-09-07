"""Pins the ingester's Kinesis batching (QNT-456 AC3): a batch respects the 500-record /
5 MB `PutRecords` limits, and a partial failure (`FailedRecordCount > 0`) retries only the
records Kinesis actually reported failed.
"""

import json

import pytest

from hyperlake.ingester import Ingester, chunk_envelope_rows, put_batch_with_retry


def _row(tid: int, coin: str = "BTC") -> dict:
    return {"tid": tid, "coin": coin, "px": "1", "sz": "1"}


def test_chunk_respects_the_max_record_count():
    rows = [_row(i) for i in range(501)]
    batches = list(chunk_envelope_rows(rows, max_records=500, max_bytes=10**9))
    assert [len(b) for b in batches] == [500, 1]


def test_chunk_respects_the_max_byte_size():
    rows = [_row(i) for i in range(10)]
    row_bytes = len(json.dumps(rows[0]).encode()) + len("BTC")
    batches = list(chunk_envelope_rows(rows, max_records=1000, max_bytes=row_bytes * 3))
    assert all(len(b) <= 3 for b in batches)
    assert sum(len(b) for b in batches) == 10


class _FlakyKinesis:
    """Fails a fixed set of record indices on the first `put_records` call, then
    succeeds -- mirrors a real transient `ProvisionedThroughputExceededException`."""

    def __init__(self, fail_indices: set[int]):
        self.calls: list[list[dict]] = []
        self._fail_indices = fail_indices
        self._call_count = 0

    def put_records(self, *, StreamName, Records):
        self._call_count += 1
        self.calls.append(Records)
        if self._call_count == 1:
            results = [
                {"ErrorCode": "ProvisionedThroughputExceededException"}
                if i in self._fail_indices
                else {"SequenceNumber": str(i), "ShardId": "shard-1"}
                for i in range(len(Records))
            ]
            return {"FailedRecordCount": len(self._fail_indices), "Records": results}
        return {
            "FailedRecordCount": 0,
            "Records": [
                {"SequenceNumber": str(i), "ShardId": "shard-1"} for i in range(len(Records))
            ],
        }


def test_retry_resends_only_the_records_kinesis_reported_failed():
    rows = [_row(i) for i in range(5)]
    kinesis = _FlakyKinesis(fail_indices={1, 3})

    result = put_batch_with_retry(kinesis, "hyperlake-trades", rows)

    assert result == {"records": 5, "failed": 0, "attempts": 2}
    assert len(kinesis.calls) == 2
    assert len(kinesis.calls[0]) == 5
    retried_tids = {json.loads(r["Data"])["tid"] for r in kinesis.calls[1]}
    assert retried_tids == {1, 3}


def test_retry_gives_up_after_max_attempts_and_reports_still_failed():
    rows = [_row(0)]

    class _AlwaysFails:
        def __init__(self):
            self.calls = 0

        def put_records(self, *, StreamName, Records):
            self.calls += 1
            return {"FailedRecordCount": 1, "Records": [{"ErrorCode": "Throttled"}]}

    kinesis = _AlwaysFails()
    result = put_batch_with_retry(kinesis, "s", rows, max_attempts=3)

    assert kinesis.calls == 3
    assert result["failed"] == 1
    assert result["attempts"] == 3


def test_ingester_flush_never_silently_drops_a_permanently_failed_record():
    """The ingestion contract is at-least-once into bronze (CLAUDE.md) -- once
    `put_batch_with_retry` exhausts its attempts, `_flush` must not swallow the loss."""

    class _AlwaysFailingKinesis:
        def put_records(self, *, StreamName, Records):
            return {
                "FailedRecordCount": len(Records),
                "Records": [{"ErrorCode": "Throttled"} for _ in Records],
            }

    ingester = Ingester(coins=["BTC"], kinesis_client=_AlwaysFailingKinesis(), stream_name="s")
    ingester._pending_rows = [_row(0)]

    with pytest.raises(RuntimeError, match="permanently failed"):
        ingester._flush()
