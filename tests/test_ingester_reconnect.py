"""Pins the ingester's reconnect + gap recording (QNT-456 AC2): a simulated
disconnect/reconnect emits exactly one gap interval with the right bounds, and every
trade -- including the replayed tail after reconnect -- reaches Kinesis; nothing is
dropped.
"""

import asyncio
import json
import time
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
        self._closed = False
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def close(self) -> None:
        self._closed = True

    async def recv(self) -> str:
        if self._closed:
            raise ConnectionError("closed locally")
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


def _trade(tid: int, time_ms: int, coin: str = "BTC") -> dict:
    return {
        "coin": coin,
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
    assert gap_events[0]["coin"] == "BTC"
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


def test_connect_logs_subscribed_with_every_watchlist_coin(capsys):
    # QNT-457 AC2: a deployment check greps CloudWatch Logs for this line to confirm every
    # watchlist coin was actually subscribed, not just that the socket opened.
    conn = _FakeConn([_trades_message([_trade(1, 1000)])])

    kinesis = _FakeKinesis()
    ingester = ing.Ingester(
        coins=["BTC", "ETH", "HYPE"],
        kinesis_client=kinesis,
        stream_name="hyperlake-trades",
        recv_timeout_s=0.02,
    )

    async def drive():
        task = asyncio.create_task(ingester.run())
        await asyncio.sleep(0.1)
        ingester.request_stop()
        await task

    with mock.patch.object(ing.websockets, "connect", return_value=conn):
        asyncio.run(drive())

    log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    subscribed = [line for line in log_lines if line["event"] == "subscribed"]

    assert len(subscribed) == 1
    assert subscribed[0]["coins"] == ["BTC", "ETH", "HYPE"]


def test_gap_tracker_closes_exactly_one_gap_per_disconnect():
    tracker = ing.GapTracker()

    assert tracker.observe_trade("BTC", 1000) is None  # first trade ever, no gap
    tracker.on_disconnect()
    gap = tracker.observe_trade("BTC", 5000)  # first trade after reconnect closes the gap

    assert gap == {"coin": "BTC", "gap_start": 1000, "gap_end": 5000, "healed": False}
    assert tracker.observe_trade("BTC", 5100) is None  # no new disconnect, no new gap


def test_each_coin_closes_on_its_own_first_post_reconnect_trade():
    # QNT-466's residual: one shared timeline closed on BTC's trade left ETH's own
    # catch-up trade 534 ms past gap_end.
    tracker = ing.GapTracker()
    tracker.observe_trade("BTC", 1000)
    tracker.observe_trade("ETH", 1100)
    tracker.on_disconnect()

    btc_gap = tracker.observe_trade("BTC", 5000)
    eth_gap = tracker.observe_trade("ETH", 5534)

    assert btc_gap == {"coin": "BTC", "gap_start": 1000, "gap_end": 5000, "healed": False}
    assert eth_gap == {"coin": "ETH", "gap_start": 1100, "gap_end": 5534, "healed": False}


def test_replayed_trade_at_or_before_gap_start_does_not_close_the_gap():
    # Every subscribe replays the coin's last 30 trades (ops runbook); on a quiet coin
    # that snapshot reaches back past gap_start, including the gap_start trade itself.
    tracker = ing.GapTracker()
    tracker.observe_trade("ETH", 1100)
    tracker.on_disconnect()

    assert tracker.observe_trade("ETH", 900) is None
    assert tracker.observe_trade("ETH", 1100) is None
    assert tracker.observe_trade("ETH", 1200) == {
        "coin": "ETH",
        "gap_start": 1100,
        "gap_end": 1200,
        "healed": False,
    }


def test_second_disconnect_before_close_keeps_the_earliest_gap_start():
    tracker = ing.GapTracker()
    tracker.observe_trade("ETH", 1100)
    tracker.observe_trade("BTC", 1000)
    tracker.on_disconnect()
    tracker.observe_trade("BTC", 3000)  # BTC closes; ETH still open
    tracker.on_disconnect()

    gap = tracker.observe_trade("ETH", 6000)

    assert gap is not None
    assert gap["gap_start"] == 1100


def test_gap_still_open_at_exit_closes_at_max_observed_event_time():
    tracker = ing.GapTracker()
    tracker.observe_trade("BTC", 1000)
    tracker.observe_trade("ETH", 1100)
    tracker.on_disconnect()
    tracker.observe_trade("BTC", 5000)  # ETH never trades again

    assert tracker.close_open_gaps() == [
        {"coin": "ETH", "gap_start": 1100, "gap_end": 5000, "healed": False}
    ]
    assert tracker.close_open_gaps() == []


def test_open_gap_with_no_later_event_is_not_emitted_zero_length():
    # Nothing traded after the disconnect at all: there is no event-time evidence for an
    # end, and a zero-length gap would be rejected by heal.gap_to_hours.
    tracker = ing.GapTracker()
    tracker.observe_trade("BTC", 1000)
    tracker.on_disconnect()

    assert tracker.close_open_gaps() == []


def test_self_exit_records_a_gap_for_a_coin_that_never_traded_after_reconnect(capsys):
    conn1 = _FakeConn(
        [_trades_message([_trade(1, 1000, "BTC"), _trade(2, 1100, "ETH")])], disconnect_after=1
    )
    conn2 = _FakeConn([_trades_message([_trade(3, 5000, "BTC")])])
    connections = iter([conn1, conn2])

    ingester = ing.Ingester(
        coins=["BTC", "ETH"],
        kinesis_client=_FakeKinesis(),
        stream_name="hyperlake-trades",
        recv_timeout_s=0.02,
        backoff_base_s=0.01,
    )

    async def drive():
        task = asyncio.create_task(ingester.run())
        await asyncio.sleep(0.3)
        ingester.request_stop()
        await task

    with mock.patch.object(
        ing.websockets, "connect", side_effect=lambda *a, **k: next(connections)
    ):
        asyncio.run(drive())

    log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    gaps = {g["coin"]: g for g in log_lines if g["event"] == "gap_recorded"}

    assert gaps["BTC"]["gap_start"] == 1000 and gaps["BTC"]["gap_end"] == 5000
    assert gaps["ETH"]["gap_start"] == 1100 and gaps["ETH"]["gap_end"] == 5000
    assert [g["event"] for g in log_lines][-1] == "self_exit"


def test_injected_disconnect_closes_the_socket_and_holds_before_reconnecting(capsys):
    conn1 = _FakeConn([_trades_message([_trade(1, 1000, "BTC"), _trade(2, 1100, "ETH")])])
    conn2 = _FakeConn(
        [_trades_message([_trade(3, 5000, "BTC")]), _trades_message([_trade(4, 5100, "ETH")])]
    )
    connections = iter([conn1, conn2])
    connect_times: list[float] = []

    def fake_connect(*args, **kwargs):
        connect_times.append(time.monotonic())
        return next(connections)

    ingester = ing.Ingester(
        coins=["BTC", "ETH"],
        kinesis_client=_FakeKinesis(),
        stream_name="hyperlake-trades",
        recv_timeout_s=0.02,
        backoff_base_s=0.01,
        inject_disconnect_after_s=0.1,
        inject_disconnect_for_s=0.2,
    )

    async def drive():
        task = asyncio.create_task(ingester.run())
        await asyncio.sleep(0.6)
        ingester.request_stop()
        await task

    with mock.patch.object(ing.websockets, "connect", side_effect=fake_connect):
        asyncio.run(drive())

    log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    events = [line["event"] for line in log_lines]
    gaps = [line for line in log_lines if line["event"] == "gap_recorded"]

    assert conn1._closed
    assert len(connect_times) == 2
    assert connect_times[1] - connect_times[0] >= 0.3  # after_s + for_s, not the 0.01 backoff
    assert events.index("disconnected") < events.index("reconnected")
    assert sorted(g["coin"] for g in gaps) == ["BTC", "ETH"]


def test_injection_is_off_by_default():
    ingester = ing.Ingester(coins=["BTC"], kinesis_client=_FakeKinesis(), stream_name="s")
    assert ingester.inject_disconnect_after_s == 0
