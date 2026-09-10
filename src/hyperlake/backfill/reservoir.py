"""Fallback backfill reader for the Reservoir daily archive (`hydromancer-reservoir`,
ADR-005), used when an official hour file is still missing 24 h after it should have
landed (manual decision -- see the runbook). Column-maps the Reservoir "all" fills
Parquet onto `official.py`'s fill shape and reuses its collapse + envelope + write logic
unchanged, so the two readers are provably identical past the mapping step.

Reservoir has reorganised its layout once already (`_pre_hip4_unification_backup/`), so
the path pattern and the 28-column schema (measured 2026-09-10 against a live
`by_dex/hyperliquid` file) are pinned: a missing column fails loud with `SchemaError`
rather than writing a null-filled row.
"""

import argparse
import json
import os
import time
from collections.abc import Iterable, Iterator
from typing import Any

import boto3
import pyarrow.parquet as pq

from hyperlake.backfill.official import (
    BronzeRow,
    collapse_fills,
    filter_watchlist,
    group_by_object_key,
    write_parquet_object,
)
from hyperlake.partitions import dt_from_event_time_ms, hour_from_event_time_ms
from hyperlake.watchlist import load_watchlist

ARCHIVE_BUCKET = "hydromancer-reservoir"
ARCHIVE_REGION = "ap-northeast-1"

# The full pinned column contract (measured 2026-09-10, `by_dex/hyperliquid/.../
# date=2026-09-03/fills.parquet`, 28 columns) -- a missing name here is layout drift,
# even in a column this reader doesn't otherwise touch.
PINNED_COLUMNS = frozenset(
    {
        "coin",
        "dex",
        "asset_class",
        "base_symbol",
        "quote_symbol",
        "price",
        "size",
        "side",
        "timestamp",
        "direction",
        "realized_pnl",
        "tx_hash",
        "order_id",
        "trade_id",
        "fee",
        "fee_token",
        "address",
        "crossed",
        "start_position",
        "client_order_id",
        "builder",
        "builder_fee",
        "deployer_fee",
        "priority_gas",
        "twap_id",
        "is_liquidation",
        "liquidation_mark_px",
        "liquidation_method",
    }
)

# The subset this reader actually projects and maps.
READ_COLUMNS = (
    "coin",
    "trade_id",
    "side",
    "price",
    "size",
    "timestamp",
    "tx_hash",
    "crossed",
    "is_liquidation",
    "fee",
)

# Reservoir spells sides out ("buy"/"sell"); the official archive (and this reader's
# shared collapse/envelope code) uses Hyperliquid's own "B"/"A" convention.
_SIDE_MAP = {"buy": "B", "sell": "A"}


class SchemaError(ValueError):
    """Raised when a Reservoir file is missing part of the pinned 28-column contract."""


class _S3RandomAccessFile:
    """Minimal seekable file-like over a requester-pays S3 object via ranged GETs.
    pyarrow's own `S3FileSystem` has no requester-pays option; this reader only needs
    enough random access for column-pruned Parquet reads, not a general filesystem."""

    def __init__(self, s3, bucket: str, key: str):
        self._s3 = s3
        self._bucket = bucket
        self._key = key
        head = s3.head_object(Bucket=bucket, Key=key, RequestPayer="requester")
        self.size = head["ContentLength"]
        self._pos = 0
        self.closed = False

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        else:
            self._pos = self.size + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        end = self.size - 1 if size is None or size < 0 else min(self._pos + size, self.size) - 1
        if end < self._pos:
            return b""
        resp = self._s3.get_object(
            Bucket=self._bucket,
            Key=self._key,
            RequestPayer="requester",
            Range=f"bytes={self._pos}-{end}",
        )
        data = resp["Body"].read()
        self._pos += len(data)
        return data

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


def dex_for_coin(coin: str) -> str:
    """Reservoir partitions fills by dex, not by a market's HIP-3 prefix directly -- a
    HIP-3 market's dex is the part of its name before the ':' (e.g. 'xyz:SP500' ->
    'xyz'); Hyperliquid-native markets (no ':') live under dex 'hyperliquid'."""
    return coin.split(":", 1)[0] if ":" in coin else "hyperliquid"


def object_key_for(dex: str, date: str) -> str:
    return f"by_dex/{dex}/fills/perp/all/date={date}/fills.parquet"


def assert_schema(columns: Iterable[str]) -> None:
    missing = PINNED_COLUMNS - set(columns)
    if missing:
        raise SchemaError(
            f"Reservoir fills.parquet is missing pinned column(s) {sorted(missing)} -- "
            "layout may have drifted (PRD 'Reservoir layout drifts' risk)"
        )


def read_fills(source) -> Iterator[dict]:
    """Column-pruned Reservoir read. Asserts the pinned schema from the file's footer
    metadata alone, before any row is read -- a missing column fails loud, never
    null-filled."""
    pf = pq.ParquetFile(source)
    assert_schema(pf.schema_arrow.names)
    for batch in pf.iter_batches(columns=list(READ_COLUMNS)):
        yield from batch.to_pylist()


def map_row(row: dict[str, Any]) -> dict[str, Any]:
    """Rename one raw Reservoir fill row onto `official.py`'s fill shape. `px`/`sz`/`fee`
    are kept as decimal-formatted strings (like the official archive's own JSON), so the
    shared `to_envelope_row` treats both sources identically."""
    try:
        side = _SIDE_MAP[row["side"]]
    except KeyError:
        raise SchemaError(
            f"Reservoir fill has unrecognised side {row['side']!r} -- expected "
            f"{sorted(_SIDE_MAP)}; layout may have drifted"
        ) from None
    return {
        "tid": row["trade_id"],
        "coin": row["coin"],
        "side": side,
        "px": str(row["price"]),
        "sz": str(row["size"]),
        "time": int(row["timestamp"].timestamp() * 1000),
        "hash": row["tx_hash"],
        "crossed": row["crossed"],
        "liquidation": row["is_liquidation"],
        "fee": str(row["fee"]) if row["fee"] is not None else None,
    }


def build_rows(collapsed: Iterable[tuple[dict, int]]) -> Iterator[BronzeRow]:
    """Unlike the official reader (one invocation = one fixed hour), a Reservoir daily
    file spans 24 h -- `hour` is derived per row from the fill's own event time."""
    for fill, archive_rows_collapsed in collapsed:
        yield BronzeRow(
            coin=fill["coin"],
            dt=dt_from_event_time_ms(fill["time"]),
            hour=hour_from_event_time_ms(fill["time"]),
            fill=fill,
            archive_rows_collapsed=archive_rows_collapsed,
        )


def run(
    date: str,
    *,
    watchlist: list[str] | None = None,
    bucket: str | None = None,
    s3_client=None,
    session_id: str | None = None,
    ingested_at_ms: int | None = None,
) -> dict[str, int]:
    """Process one Reservoir daily file per watchlist dex and write it to bronze.
    Returns a summary (`objects_written`, `rows_written`) for the CLI report line."""
    watchlist = watchlist if watchlist is not None else load_watchlist()
    s3 = s3_client or boto3.client("s3", region_name=ARCHIVE_REGION)
    bucket = bucket or os.environ["HYPERLAKE_DATA_BUCKET"]
    session_id = session_id or f"backfill-reservoir-{date}"
    ingested_at_ms = ingested_at_ms if ingested_at_ms is not None else int(time.time() * 1000)

    # Materialized in full, unlike official.py's one-hour generator pipeline: this reader
    # runs locally (not under the Lambda's 2 GB bound) against a per-dex, watchlist-filtered
    # slice of one day, not a whole archive hour.
    mapped_fills: list[dict] = []
    for dex in sorted({dex_for_coin(coin) for coin in watchlist}):
        source = _S3RandomAccessFile(s3, ARCHIVE_BUCKET, object_key_for(dex, date))
        raw_fills = filter_watchlist(read_fills(source), watchlist)
        mapped_fills.extend(map_row(row) for row in raw_fills)

    rows = build_rows(collapse_fills(mapped_fills))
    by_key = group_by_object_key(rows, ingested_at_ms=ingested_at_ms, session_id=session_id)

    for key, envelope_rows in by_key.items():
        write_parquet_object(s3, bucket, key, envelope_rows)

    return {
        "objects_written": len(by_key),
        "rows_written": sum(len(v) for v in by_key.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="archive date, YYYY-MM-DD")
    args = parser.parse_args()
    summary = run(date=args.date)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
