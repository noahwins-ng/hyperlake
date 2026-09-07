#!/usr/bin/env python3
"""Push fixture envelope records to the QNT-455 spike's Kinesis stream.

Generates records for every watchlist market, matching the ingester-owned envelope
(src/hyperlake/envelope.py). `time`/`ingested_at` are Unix epoch milliseconds -- the
Firehose delivery stream's `hive_json_ser_de` is configured with `timestamp_formats =
["millis"]` to parse them as such. One record per coin is backdated 2 days, to prove
dynamic partitioning keys `dt` off event `time`, not arrival (AC2); the `xyz:SP500` records
prove the ':' -> '_' partition-value normalisation (AC3).

Usage:
    uv run scripts/spike/put_records.py --stream-name hyperlake-trades
"""

import argparse
import json
import random
import time

import boto3

from hyperlake.watchlist import load_watchlist

RECORDS_PER_COIN = 100
# Clear of the archive's observed tid magnitude (OQ-1 spike: 4.3e14-1.1e15) so a fixture
# run can never collide with a real trade's tid in the silver merge.
FIXTURE_TID_BASE = 9_000_000_000_000_000
BACKDATE_DAYS_MS = 2 * 24 * 60 * 60 * 1000


def _fixture_record(tid: int, coin: str, now_ms: int, backdated: bool) -> dict:
    event_ms = now_ms - BACKDATE_DAYS_MS if backdated else now_ms
    return {
        "tid": tid,
        "coin": coin,
        "side": random.choice(["B", "A"]),
        "px": round(random.uniform(1, 100_000), 8),
        "sz": round(random.uniform(0.001, 10), 6),
        "time": event_ms,
        "hash": None,
        "crossed": random.choice([True, False]),
        "liquidation": False,
        "fee": None,
        "raw_payload": json.dumps({"tid": tid, "coin": coin, "spike": "QNT-455"}),
        "source": "ws",
        "ingested_at": now_ms,
        "session_id": "qnt455-spike",
        "archive_rows_collapsed": 0,
    }


def build_records(coins: list[str]) -> list[dict]:
    now_ms = int(time.time() * 1000)
    records = []
    tid = FIXTURE_TID_BASE
    for coin in coins:
        for i in range(RECORDS_PER_COIN):
            records.append(_fixture_record(tid, coin, now_ms, backdated=(i == 0)))
            tid += 1
    return records


def put_records(stream_name: str, records: list[dict]) -> None:
    client = boto3.client("kinesis", region_name="ap-northeast-1")
    failures = 0
    for start in range(0, len(records), 100):
        batch = records[start : start + 100]
        entries = [{"Data": json.dumps(r).encode(), "PartitionKey": r["coin"]} for r in batch]
        resp = client.put_records(StreamName=stream_name, Records=entries)
        failures += resp["FailedRecordCount"]
    print(f"put {len(records)} records to {stream_name!r} ({failures} failed)")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--stream-name", default="hyperlake-trades")
    p.add_argument("--coins", nargs="+", default=load_watchlist())
    a = p.parse_args()
    records = build_records(a.coins)
    put_records(a.stream_name, records)
    backdated_days = BACKDATE_DAYS_MS // 86_400_000
    for coin in a.coins:
        print(f"  {coin:12s} {RECORDS_PER_COIN} records (1 backdated {backdated_days}d)")


if __name__ == "__main__":
    main()
