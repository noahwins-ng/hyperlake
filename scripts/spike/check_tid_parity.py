#!/usr/bin/env python3
"""OQ-1 hard gate: every WS-captured trade must appear in the official archive with the
same `tid` and identical `coin, px, sz, side, time`.

Reads a capture from capture_ws_trades.py, works out which hourly archive files cover it,
downloads them (requester-pays, ~46 MB LZ4 each), and compares.

Archive layout (measured 2026-09-04, see docs/spikes/2026-09-04-oq1-archive-desk-spike.md):
    s3://hl-mainnet-node-data/node_fills_by_block/hourly/YYYYMMDD/H.lz4
    one JSON line per block: {"block_time", "block_number", "events": [[user, fill], ...]}
    fill keys: coin px sz side time tid hash oid crossed fee dir closedPnl ...
Each trade appears as up to two fills (maker + taker) sharing a tid.

Usage:
    python check_tid_parity.py --capture capture.jsonl [--profile default] [--cache ./cache]
Exit code 0 = gate passes, 1 = at least one captured tid missing or mismatched,
2 = an hour file has not landed yet (no verdict; re-run later).
"""
import argparse
import io
import json
import os
import sys
import time

import boto3
import lz4.frame

BUCKET = "hl-mainnet-node-data"
PREFIX = "node_fills_by_block/hourly"
COMPARE_KEYS = ("coin", "px", "sz", "side", "time")
BOUNDARY_SLACK_S = 300   # trades this close to the hour end may sit in the next hour file


def load_capture(path: str) -> tuple[list[dict], dict]:
    trades, header = [], {}
    with open(path) as f:
        for line in f:
            row = json.loads(line)
            if row.get("_header"):
                header = row
            elif row.get("_footer"):
                continue
            else:
                trades.append(row)
    if not trades:
        sys.exit("capture contains no trades")
    return trades, header


def hour_keys_for(trades: list[dict]) -> list[str]:
    """Archive hour files are cut by *block arrival* time, not trade `time`: a trade at
    11:59:59.9 was found in 12.lz4 (measured 2026-09-04). So fetch each trade's hour AND
    the following hour."""
    hours: set[str] = set()
    for t in trades:
        secs = t["time"] / 1000
        hours.add(time.strftime("%Y%m%d/%H", time.gmtime(secs)))
        if secs % 3600 >= 3600 - BOUNDARY_SLACK_S:      # near the top of the hour
            hours.add(time.strftime("%Y%m%d/%H", time.gmtime(secs + 3600)))
    return [f"{PREFIX}/{h}.lz4" for h in sorted(hours)]


def fetch(s3, key: str, cache_dir: str | None) -> bytes:
    if cache_dir:
        local = os.path.join(cache_dir, key.replace("/", "_"))
        if os.path.exists(local):
            return open(local, "rb").read()
    obj = s3.get_object(Bucket=BUCKET, Key=key, RequestPayer="requester")
    data = obj["Body"].read()
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        open(local, "wb").write(data)
    return data


def index_archive(blob: bytes, wanted: set[int]) -> dict[int, dict]:
    """Return {tid: fill} for wanted tids only, preferring the **taker** fill
    (`crossed = true`). The maker/taker pair shares px/sz/time but has opposite `side`;
    the WS `trades` feed reports the taker's side. (This is the PRD's bronze collapse
    rule: keep the crossed fill as the canonical row.)"""
    found: dict[int, dict] = {}
    with lz4.frame.open(io.BytesIO(blob), mode="rt") as f:
        for line in f:
            block = json.loads(line)
            for _user, fill in block["events"]:
                tid = fill.get("tid")
                if tid not in wanted:
                    continue
                if tid not in found or (fill.get("crossed") and not found[tid].get("crossed")):
                    found[tid] = fill
    return found


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--capture", required=True)
    p.add_argument("--profile", default=None, help="AWS profile (default: env/default chain)")
    p.add_argument("--cache", default=None, help="dir to cache downloaded hour files")
    a = p.parse_args()

    trades, header = load_capture(a.capture)
    by_tid = {t["tid"]: t for t in trades}
    keys = hour_keys_for(trades)
    print(f"captured {len(trades)} trades ({len(by_tid)} distinct tid) across {len(keys)} archive hour(s)")

    session = boto3.Session(profile_name=a.profile) if a.profile else boto3.Session()
    s3 = session.client("s3", region_name="ap-northeast-1")

    found: dict[int, dict] = {}
    not_landed = []
    for key in keys:
        try:
            blob = fetch(s3, key, a.cache)
        except s3.exceptions.NoSuchKey:
            not_landed.append(key)
            print(f"  not landed yet: s3://{BUCKET}/{key} (lands ~1h after the hour closes)")
            continue
        print(f"  scanning s3://{BUCKET}/{key} ({len(blob) / 1e6:.0f} MB LZ4)")
        found.update(index_archive(blob, set(by_tid) - set(found)))
    if not_landed:
        print(f"\nNOT READY: {len(not_landed)} hour file(s) missing; re-run later. No verdict.")
        sys.exit(2)

    missing = [tid for tid in by_tid if tid not in found]
    mismatched = []
    for tid, fill in found.items():
        ws = by_tid[tid]
        diffs = {k: (ws.get(k), fill.get(k)) for k in COMPARE_KEYS if str(ws.get(k)) != str(fill.get(k))}
        if diffs:
            mismatched.append((tid, diffs))

    print()
    print(f"matched   : {len(found) - len(mismatched)}")
    print(f"mismatched: {len(mismatched)}")
    print(f"missing   : {len(missing)}")
    for tid, diffs in mismatched[:10]:
        print(f"  tid {tid}: {diffs}")
    for tid in missing[:10]:
        t = by_tid[tid]
        print(f"  tid {tid} ({t['coin']} @ {t['time']}) not in archive")

    if missing or mismatched:
        print("\nGATE: FAIL - see docs/prd.md OQ-1 (plan B: hash-based key)")
        sys.exit(1)
    print("\nGATE: PASS - tid parity holds; OQ-1 can close with an ADR")


if __name__ == "__main__":
    main()
