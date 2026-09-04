#!/usr/bin/env python3
"""Capture live Hyperliquid WS `trades` messages for the OQ-1 tid-parity gate.

Writes one JSON line per trade, exactly as received, plus a header line with the capture
window so check_tid_parity.py knows which archive hour files to fetch.

Usage:
    python capture_ws_trades.py --seconds 90 --out capture.jsonl
    python capture_ws_trades.py --coins BTC ETH xyz:SP500 --seconds 60 --out capture.jsonl
"""

import argparse
import asyncio
import json
import time

import websockets

WS_URL = "wss://api.hyperliquid.xyz/ws"
DEFAULT_COINS = ["BTC", "ETH", "HYPE", "xyz:SP500", "xyz:XYZ100"]


async def capture(coins: list[str], seconds: int, out_path: str) -> None:
    started_ms = int(time.time() * 1000)
    counts: dict[str, int] = {c: 0 for c in coins}
    with open(out_path, "w") as out:
        async with websockets.connect(WS_URL, max_size=None) as ws:
            for coin in coins:
                await ws.send(
                    json.dumps(
                        {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}
                    )
                )
            out.write(
                json.dumps(
                    {"_header": True, "started_ms": started_ms, "coins": coins, "seconds": seconds}
                )
                + "\n"
            )
            deadline = time.time() + seconds
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("channel") != "trades":
                    continue
                for trade in msg["data"]:
                    counts[trade["coin"]] = counts.get(trade["coin"], 0) + 1
                    out.write(json.dumps(trade) + "\n")
            out.write(
                json.dumps({"_footer": True, "ended_ms": int(time.time() * 1000), "counts": counts})
                + "\n"
            )
    total = sum(counts.values())
    print(f"captured {total} trades over {seconds}s -> {out_path}")
    for coin, n in counts.items():
        print(f"  {coin:12s} {n}")
    first_hour = time.strftime("%Y%m%d/%H", time.gmtime(started_ms / 1000))
    print(f"archive hour file(s) start at: node_fills_by_block/hourly/{first_hour}.lz4")
    print("run check_tid_parity.py ~2h after the capture hour closes.")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--coins", nargs="+", default=DEFAULT_COINS)
    p.add_argument("--seconds", type=int, default=90)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    asyncio.run(capture(a.coins, a.seconds, a.out))


if __name__ == "__main__":
    main()
