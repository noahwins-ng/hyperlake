"""Live WebSocket ingester (FR-1): single containerised consumer of the
HyperCore `trades` channel for the watchlist. Wraps every trade in the shared bronze
envelope, batches to Kinesis `PutRecords` (partition key `coin`), retries only failed
records, reconnects with backoff while recording gap intervals, and self-exits after
`--max-session-hours`.

The WS `trades` message carries fewer fields than the archive fill (measured 2026-09-04
spike, PRD Data model): `coin, side, px, sz, time, hash, tid, users[2]`, no `crossed`,
`liquidation`, or `fee`. Those typed columns stay null for `source = "ws"` rows; the
archive replay (backfill Lambda) populates them at the silver merge. `users` is not a
typed envelope column, so it survives only inside `raw_payload`.
"""

import argparse
import asyncio
import json
import signal
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import uuid4

import websockets

from hyperlake.watchlist import load_watchlist

WS_URL = "wss://api.hyperliquid.xyz/ws"
DEFAULT_MAX_SESSION_HOURS = 6.0

# Kinesis PutRecords hard limits (AWS API): 500 records or 5 MB per call, whichever
# comes first. The 5 MB estimate is deliberately conservative (raw UTF-8 byte length of
# `Data` + `PartitionKey`, no protocol framing) so a batch built to this bound never gets
# rejected as oversized.
KINESIS_MAX_RECORDS = 500
KINESIS_MAX_BYTES = 5 * 1024 * 1024

RECONNECT_BACKOFF_BASE_S = 1.0
RECONNECT_BACKOFF_MAX_S = 30.0


def log_event(event: str, **fields: Any) -> None:
    """One structured JSON line to stdout -- the only sink; `session-down` (its own
    ticket) collects these from CloudWatch Logs for the session manifest."""
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def to_envelope_row(trade: dict, *, ingested_at_ms: int, session_id: str) -> dict[str, Any]:
    """Build the shared bronze envelope (`hyperlake.envelope.SCHEMA`) from one WS
    `trades` message. Mirrors `backfill.official.to_envelope_row`'s field mapping for the
    columns both paths can populate; archive-only columns are null here by construction.
    """
    return {
        "tid": trade["tid"],
        "coin": trade["coin"],
        "side": trade["side"],
        "px": Decimal(str(trade["px"])),
        "sz": Decimal(str(trade["sz"])),
        "time": trade["time"],
        "hash": trade.get("hash"),
        "crossed": None,
        "liquidation": None,
        "fee": None,
        "raw_payload": json.dumps(trade, sort_keys=True),
        "source": "ws",
        "ingested_at": ingested_at_ms,
        "session_id": session_id,
        "archive_rows_collapsed": None,
    }


def _record_size(record: dict[str, Any]) -> int:
    data = json.dumps(record, default=str).encode()
    return len(data) + len(record["coin"].encode())


def chunk_envelope_rows(
    rows: Iterable[dict[str, Any]],
    *,
    max_records: int = KINESIS_MAX_RECORDS,
    max_bytes: int = KINESIS_MAX_BYTES,
) -> Iterator[list[dict[str, Any]]]:
    """Group envelope rows into Kinesis `PutRecords` batches within the 500-record /
    5 MB-per-call limits."""
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    for row in rows:
        row_bytes = _record_size(row)
        if batch and (len(batch) >= max_records or batch_bytes + row_bytes > max_bytes):
            yield batch
            batch, batch_bytes = [], 0
        batch.append(row)
        batch_bytes += row_bytes
    if batch:
        yield batch


def _kinesis_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {"Data": json.dumps(row, default=str).encode(), "PartitionKey": row["coin"]}


def put_batch_with_retry(
    client, stream_name: str, records: list[dict[str, Any]], *, max_attempts: int = 5
) -> dict[str, int]:
    """`PutRecords` one batch, retrying only the entries Kinesis reports failed
    (`FailedRecordCount` / per-record `ErrorCode` -- AC3), up to `max_attempts`. Logs
    per-batch latency (G2a)."""
    pending = records
    total_failed_finally = 0
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        resp = client.put_records(
            StreamName=stream_name, Records=[_kinesis_entry(r) for r in pending]
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        failed_count = resp["FailedRecordCount"]
        log_event(
            "put_records",
            stream=stream_name,
            records=len(pending),
            failed=failed_count,
            latency_ms=latency_ms,
            attempt=attempt,
        )
        if failed_count == 0:
            return {"records": len(records), "failed": 0, "attempts": attempt}
        pending = [
            row
            for row, result in zip(pending, resp["Records"], strict=True)
            if "ErrorCode" in result
        ]
        total_failed_finally = failed_count
        if attempt == max_attempts:
            break
    return {"records": len(records), "failed": total_failed_finally, "attempts": max_attempts}


@dataclass
class GapTracker:
    """Per-coin gap bounds across reconnects. The one WS connection carries every
    watchlist coin, but each coin resumes on its own first post-reconnect trade, so one
    shared timeline closed by whichever coin traded first left other coins' catch-up
    trades outside the recorded gap (2026-09-11 live session, docs/spikes)."""

    last_trade_time_ms: dict[str, int] = field(default_factory=dict)
    _open_gap_start_ms: dict[str, int] = field(default_factory=dict)

    def observe_trade(self, coin: str, time_ms: int) -> dict[str, Any] | None:
        """Record a trade's time; if it is this coin's first trade after a disconnect,
        close and return that coin's gap. Only a trade strictly after `gap_start` closes
        it: every subscribe replays the coin's last 30 trades (ops runbook), which on a
        quiet coin reach back to and past `gap_start`. Replayed trades are never
        dropped -- this only tracks bounds, the caller still emits every trade."""
        closed_gap = None
        gap_start = self._open_gap_start_ms.get(coin)
        if gap_start is not None and time_ms > gap_start:
            closed_gap = _gap(coin, gap_start, time_ms)
            del self._open_gap_start_ms[coin]
        if coin not in self.last_trade_time_ms or time_ms > self.last_trade_time_ms[coin]:
            self.last_trade_time_ms[coin] = time_ms
        return closed_gap

    def on_disconnect(self) -> None:
        """Open a gap for every coin seen so far, at that coin's last trade time. A coin
        whose gap from an earlier drop is still open keeps its earlier start."""
        for coin, last in self.last_trade_time_ms.items():
            self._open_gap_start_ms.setdefault(coin, last)

    def close_open_gaps(self) -> list[dict[str, Any]]:
        """At self-exit, close every still-open gap at the max observed event time so a
        coin that never traded again is not dropped. A gap with no later event anywhere
        has no evidenced end and is skipped (heal rejects a zero-length gap)."""
        max_time = max(self.last_trade_time_ms.values(), default=None)
        gaps = [
            _gap(coin, start, max_time)
            for coin, start in self._open_gap_start_ms.items()
            if max_time is not None and max_time > start
        ]
        self._open_gap_start_ms.clear()
        return gaps


def _gap(coin: str, gap_start: int, gap_end: int) -> dict[str, Any]:
    return {"coin": coin, "gap_start": gap_start, "gap_end": gap_end, "healed": False}


def subscribe_messages(coins: list[str]) -> list[str]:
    return [
        json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}})
        for coin in coins
    ]


def envelope_rows_from_message(raw: str | bytes) -> list[dict]:
    """Parse one WS frame into its `trades` payload, or `[]` for a non-trades message
    (e.g. the subscription ack)."""
    msg = json.loads(raw)
    if msg.get("channel") != "trades":
        return []
    return msg["data"]


class Ingester:
    def __init__(
        self,
        *,
        coins: list[str],
        kinesis_client,
        stream_name: str,
        max_session_hours: float = DEFAULT_MAX_SESSION_HOURS,
        session_id: str | None = None,
        recv_timeout_s: float = 5.0,
        backoff_base_s: float = RECONNECT_BACKOFF_BASE_S,
        backoff_max_s: float = RECONNECT_BACKOFF_MAX_S,
        inject_disconnect_after_s: float = 0,
        inject_disconnect_for_s: float = 0,
    ) -> None:
        self.coins = coins
        self.kinesis_client = kinesis_client
        self.stream_name = stream_name
        self.max_session_hours = max_session_hours
        self.session_id = session_id or f"ws-{uuid4().hex[:12]}"
        self.recv_timeout_s = recv_timeout_s
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.inject_disconnect_after_s = inject_disconnect_after_s
        self.inject_disconnect_for_s = inject_disconnect_for_s
        self._inject_pending = inject_disconnect_after_s > 0
        self._inject_hold_s: float | None = None
        self.gap_tracker = GapTracker()
        self._pending_rows: list[dict[str, Any]] = []
        self._stop = asyncio.Event()
        self._started_monotonic = time.monotonic()

    def request_stop(self) -> None:
        self._stop.set()

    def _session_expired(self) -> bool:
        return (time.monotonic() - self._started_monotonic) >= self.max_session_hours * 3600

    def _inject_due(self) -> bool:
        return (
            self._inject_pending
            and (time.monotonic() - self._started_monotonic) >= self.inject_disconnect_after_s
        )

    def _flush(self) -> None:
        if not self._pending_rows:
            return
        rows, self._pending_rows = self._pending_rows, []
        for batch in chunk_envelope_rows(rows):
            result = put_batch_with_retry(self.kinesis_client, self.stream_name, batch)
            if result["failed"] > 0:
                # Never drop silently: the ingestion contract is at-least-once into
                # bronze (CLAUDE.md), and there is no further in-process retry path once
                # `put_batch_with_retry` gives up. Raising surfaces the loss loudly
                # (process exit) instead of a stdout line a human has to notice.
                raise RuntimeError(
                    f"Kinesis PutRecords permanently failed for {result['failed']} "
                    f"record(s) after {result['attempts']} attempts"
                )

    def _ingest_trades(self, trades: list[dict]) -> None:
        now_ms = int(time.time() * 1000)
        for trade in trades:
            gap = self.gap_tracker.observe_trade(trade["coin"], trade["time"])
            if gap is not None:
                log_event("gap_recorded", **gap)
            self._pending_rows.append(
                to_envelope_row(trade, ingested_at_ms=now_ms, session_id=self.session_id)
            )
        self._flush()

    async def run(self) -> None:
        backoff = self.backoff_base_s
        first_connect = True
        while not self._stop.is_set():
            if self._session_expired():
                break
            try:
                async with websockets.connect(WS_URL, max_size=None) as ws:
                    for sub in subscribe_messages(self.coins):
                        await ws.send(sub)
                    # The one place a deployment check can confirm every
                    # watchlist coin was actually subscribed, not just that the socket opened.
                    log_event("subscribed", coins=self.coins, session_id=self.session_id)
                    if not first_connect:
                        log_event("reconnected", session_id=self.session_id)
                    first_connect = False
                    backoff = self.backoff_base_s
                    while not self._stop.is_set():
                        if self._session_expired():
                            break
                        if self._inject_due():
                            # One-shot test gap: a real close, so the normal
                            # disconnect/backoff/gap path below runs, not a simulation of it.
                            self._inject_pending = False
                            self._inject_hold_s = self.inject_disconnect_for_s
                            log_event("inject_disconnect", hold_s=self._inject_hold_s)
                            await ws.close()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.recv_timeout_s)
                        except TimeoutError:
                            continue
                        try:
                            self._ingest_trades(envelope_rows_from_message(raw))
                        except (KeyError, TypeError, ValueError) as exc:
                            # PRD Risks: WS schema drift degrades to null typed fields,
                            # it never kills the session -- a malformed/unexpected frame
                            # is logged and skipped rather than crashing the process.
                            log_event("message_error", error=str(exc))
            except (websockets.WebSocketException, OSError) as exc:
                self.gap_tracker.on_disconnect()
                delay = backoff if self._inject_hold_s is None else self._inject_hold_s
                self._inject_hold_s = None
                log_event("disconnected", error=str(exc), retry_in_s=delay)
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, self.backoff_max_s)
        self._flush()
        for gap in self.gap_tracker.close_open_gaps():
            log_event("gap_recorded", **gap)
        log_event("self_exit", session_id=self.session_id, reason="max_session_hours_or_stop")


async def _run_with_signal_handling(ingester: Ingester) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, ingester.request_stop)
    await ingester.run()


def main() -> None:
    import boto3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream-name", required=True)
    parser.add_argument(
        "--max-session-hours",
        type=float,
        default=DEFAULT_MAX_SESSION_HOURS,
        help="self-exit after this many hours (default: %(default)s)",
    )
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument(
        "--inject-disconnect-after-s",
        type=float,
        default=0,
        help="test only: close the socket once, this long after start (default: off)",
    )
    parser.add_argument(
        "--inject-disconnect-for-s",
        type=float,
        default=0,
        help="test only: stay disconnected this long before reconnecting",
    )
    args = parser.parse_args()

    kinesis_client = boto3.client("kinesis", region_name=args.region)
    ingester = Ingester(
        coins=load_watchlist(),
        kinesis_client=kinesis_client,
        stream_name=args.stream_name,
        max_session_hours=args.max_session_hours,
        inject_disconnect_after_s=args.inject_disconnect_after_s,
        inject_disconnect_for_s=args.inject_disconnect_for_s,
    )
    asyncio.run(_run_with_signal_handling(ingester))


if __name__ == "__main__":
    main()
