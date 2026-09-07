"""Pins the ingester's reconnect + gap recording (QNT-456 AC2): a simulated
disconnect/reconnect emits exactly one gap interval with the right bounds, and every
trade -- including the replayed tail after reconnect -- reaches Kinesis; nothing is
dropped.
"""

import asyncio
import json
from unittest import mock

from hyperlake import ingester as ing


def _trades_message(trades: list[dict]) -> str:
    return json.dumps({"channel": "trades", "data": trades})


class _FakeConn:
    """Stands in for `websockets.connect(...)`'s `async with` context manager. Replays
    `messages` one per `recv()`; once exhausted (or at `disconnect_after`), blocks until
    the test's short `recv_timeout_s` times it out, so the ingester's stop flag gets
    rechecked promptly instead of the test waiting on a real socket timeout."""

    def __init__(self, messages: list[str], disconnect_after: int | None = None):
        self._messages = messages
        self._disconnect_after = disconnect_after
        self._recv_count = 0
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def recv(self) -> str:
        if self._disconnect_after is not None and self._recv_count == self._disconnect_after:
            raise ConnectionError("simulated WS drop")
        if self._recv_count >= len(self._messages):
            await asyncio.Event().wait()  # never resolves; caller wraps in wait_for
        msg = self._messages[self._recv_count]
        self._recv_count += 1
        return msg


class _FakeKinesis:
    def __init__(self):
        self.put_calls: list[list[dict]] = []

    def put_records(self, *, StreamName, Records):
        self.put_calls.append(Records)
        return {
            "FailedRecordCount": 0,
            "Records": [{"SequenceNumber": "x", "ShardId": "y"} for _ in Records],
        }


def _trade(tid: int, time_ms: int) -> dict:
    return {
        "coin": "BTC",
        "side": "B",
        "px": "1",
        "sz": "1",
        "time": time_ms,
        "hash": "h",
        "tid": tid,
    }


def test_reconnect_emits_exactly_one_gap_and_drops_no_trades(capsys):
    before_disconnect = _trade(1, 1000)
    after_reconnect = _trade(2, 5000)  # the replayed tail trade

    conn1 = _FakeConn([_trades_message([before_disconnect])], disconnect_after=1)
    conn2 = _FakeConn([_trades_message([after_reconnect])])
    connections = iter([conn1, conn2])

    kinesis = _FakeKinesis()
    ingester = ing.Ingester(
        coins=["BTC"],
        kinesis_client=kinesis,
        stream_name="hyperlake-trades",
        recv_timeout_s=0.02,
        backoff_base_s=0.01,
    )

    async def drive():
        task = asyncio.create_task(ingester.run())
        await asyncio.sleep(0.3)
        ingester.request_stop()
        await task

    def fake_connect(*args, **kwargs):
        return next(connections)

    with mock.patch.object(ing.websockets, "connect", side_effect=fake_connect):
        asyncio.run(drive())

    log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    gap_events = [line for line in log_lines if line["event"] == "gap_recorded"]

    assert len(gap_events) == 1
    assert gap_events[0]["gap_start"] == 1000
    assert gap_events[0]["gap_end"] == 5000
    assert gap_events[0]["healed"] is False

    delivered_tids = {json.loads(r["Data"])["tid"] for batch in kinesis.put_calls for r in batch}
    assert delivered_tids == {1, 2}


def test_handshake_failure_backs_off_and_reconnects_instead_of_crashing(capsys):
    # `InvalidHandshake` (e.g. a 429/5xx during the WS upgrade) is a `WebSocketException`
    # but NOT an `OSError` -- catching only `(ConnectionClosed, OSError)` let it escape
    # the reconnect loop entirely and crash the whole session.
    trade = _trade(1, 1000)
    conn2 = _FakeConn([_trades_message([trade])])
    call_count = {"n": 0}

    def fake_connect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ing.websockets.InvalidHandshake("simulated 429")
        return conn2

    kinesis = _FakeKinesis()
    ingester = ing.Ingester(
        coins=["BTC"],
        kinesis_client=kinesis,
        stream_name="hyperlake-trades",
        recv_timeout_s=0.02,
        backoff_base_s=0.01,
    )

    async def drive():
        task = asyncio.create_task(ingester.run())
        await asyncio.sleep(0.2)
        ingester.request_stop()
        await task

    with mock.patch.object(ing.websockets, "connect", side_effect=fake_connect):
        asyncio.run(drive())  # would raise InvalidHandshake uncaught before the fix

    log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(line["event"] == "disconnected" for line in log_lines)
    delivered_tids = {json.loads(r["Data"])["tid"] for batch in kinesis.put_calls for r in batch}
    assert delivered_tids == {1}


def test_malformed_trade_is_logged_and_skipped_not_a_crash(capsys):
    # PRD Risks: WS schema drift degrades to null typed fields, it never kills the
    # session. A trade missing a required field (here: `tid`) must not crash `run()`.
    malformed = {"coin": "BTC", "side": "B", "px": "1", "sz": "1", "time": 1000, "hash": "h"}
    good = _trade(2, 2000)
    conn = _FakeConn([_trades_message([malformed]), _trades_message([good])])

    kinesis = _FakeKinesis()
    ingester = ing.Ingester(
        coins=["BTC"],
        kinesis_client=kinesis,
        stream_name="hyperlake-trades",
        recv_timeout_s=0.02,
    )

    async def drive():
        task = asyncio.create_task(ingester.run())
        await asyncio.sleep(0.2)
        ingester.request_stop()
        await task

    with mock.patch.object(ing.websockets, "connect", return_value=conn):
        asyncio.run(drive())

    log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(line["event"] == "message_error" for line in log_lines)
    delivered_tids = {json.loads(r["Data"])["tid"] for batch in kinesis.put_calls for r in batch}
    assert delivered_tids == {2}


def test_gap_tracker_closes_exactly_one_gap_per_disconnect():
    tracker = ing.GapTracker()

    assert tracker.observe_trade(1000) is None  # first trade ever, no gap
    tracker.on_disconnect()
    gap = tracker.observe_trade(5000)  # first trade after reconnect closes the gap

    assert gap == {"gap_start": 1000, "gap_end": 5000, "healed": False}
    assert tracker.observe_trade(5100) is None  # no new disconnect, no new gap
