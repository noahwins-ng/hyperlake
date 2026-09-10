"""Backfill reader for the official `hl-mainnet-node-data` archive (ADR-005 primary
source). One invocation = one hour file: stream-decode LZ4, filter to the watchlist,
collapse the maker/taker fill pair per `tid` (ADR-003 grain rule), and write bronze
Parquet at a deterministic key so re-runs overwrite rather than append.

Hour files are cut by block *arrival* time, so file `hour` typically holds events whose
own `time` falls in `hour - 1` -- `dt` is always derived from the fill's `time`, never
from the file's hour, while the object key's `hour=` segment is the literal invocation
hour (this reader never re-derives it per row).
"""

import argparse
import io
import json
import os
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import boto3
import lz4.frame
import pyarrow as pa
import pyarrow.parquet as pq

from hyperlake.envelope import SCHEMA
from hyperlake.partitions import coin_partition_value, dt_from_event_time_ms
from hyperlake.watchlist import load_watchlist

ARCHIVE_BUCKET = "hl-mainnet-node-data"
ARCHIVE_PREFIX = "node_fills_by_block/hourly"
ARCHIVE_REGION = "ap-northeast-1"


class ReadableStream(Protocol):
    """The one capability this reader needs from an S3 body -- sized reads. Narrower
    than `BinaryIO` on purpose: a real `botocore` `StreamingBody` (and this module's own
    test double) doesn't implement the rest of that protocol (e.g. `seekable`)."""

    def read(self, size: int = ...) -> bytes: ...


def read_hour_file(stream: ReadableStream) -> Iterator[dict]:
    """Stream-decode an hour file's LZ4 frame into block dicts. `stream` is consumed via
    sized reads only (`lz4.frame.open` pulls chunks as needed) -- never `.read()` with no
    size, so a ~235 MB decompressed hour file never sits in memory whole."""
    # `mode="rb"`, not "rt": the text-mode wrapper probes the underlying stream for
    # `.seekable()`, which a real `botocore` `StreamingBody` (and this reader's own
    # streaming contract) doesn't support.
    with lz4.frame.open(stream, mode="rb") as f:
        for line in f:
            yield json.loads(line)


def iter_fills(blocks: Iterable[dict]) -> Iterator[dict]:
    """Flatten `{events: [[user, fill], ...]}` blocks into individual fill dicts."""
    for block in blocks:
        for _user, fill in block["events"]:
            yield fill


def filter_watchlist(fills: Iterable[dict], watchlist: Iterable[str]) -> Iterator[dict]:
    allowed = set(watchlist)
    for fill in fills:
        if fill["coin"] in allowed:
            yield fill


def _collapse_group(group: list[dict]) -> tuple[dict, int]:
    if len(group) == 1:
        return group[0], 1
    crossed = [f for f in group if f.get("crossed")]
    if not crossed:
        # ADR-005's measured grain rule guarantees exactly one crossed=true fill in a
        # pair -- silently picking a side here would risk a wrong `side` on a row we
        # can't tell is wrong. Fail loudly instead: this is the schema-drift signal
        # ADR-005 says the reader must catch if the archive format ever changes.
        raise ValueError(
            f"tid {group[0]['tid']} has {len(group)} fills but none is crossed=true "
            "-- archive grain rule (ADR-005) may have changed"
        )
    return crossed[0], len(group)


def collapse_fills(fills: Iterable[dict]) -> Iterator[tuple[dict, int]]:
    """Group fills by `tid` and collapse each pair to the `crossed=true` (taker) side,
    or keep the sole fill. `px`/`sz` are identical across a pair -- never summed."""
    groups: dict[int, list[dict]] = {}
    for fill in fills:
        groups.setdefault(fill["tid"], []).append(fill)
    for group in groups.values():
        yield _collapse_group(group)


@dataclass
class BronzeRow:
    coin: str
    dt: str
    hour: str
    fill: dict
    archive_rows_collapsed: int


def build_rows(collapsed: Iterable[tuple[dict, int]], hour: str) -> Iterator[BronzeRow]:
    for fill, archive_rows_collapsed in collapsed:
        yield BronzeRow(
            coin=fill["coin"],
            dt=dt_from_event_time_ms(fill["time"]),
            hour=hour,
            fill=fill,
            archive_rows_collapsed=archive_rows_collapsed,
        )


def row_object_key(row: BronzeRow) -> str:
    coin_value = coin_partition_value(row.coin)
    return f"bronze/coin={coin_value}/dt={row.dt}/source=backfill/hour={row.hour}.parquet"


def to_envelope_row(row: BronzeRow, *, ingested_at_ms: int, session_id: str) -> dict[str, Any]:
    fill = row.fill
    return {
        "tid": fill["tid"],
        "coin": fill["coin"],
        "side": fill["side"],
        "px": Decimal(fill["px"]),
        "sz": Decimal(fill["sz"]),
        "time": fill["time"],
        "hash": fill.get("hash"),
        "crossed": fill.get("crossed"),
        # The official archive carries no liquidation flag (spike 2026-09-04), so this is
        # always None here; the Reservoir fallback reader (QNT-465) populates a real value
        # through this same function.
        "liquidation": fill.get("liquidation"),
        "fee": Decimal(fill["fee"]) if fill.get("fee") is not None else None,
        "raw_payload": json.dumps(fill, sort_keys=True),
        "source": "backfill",
        "ingested_at": ingested_at_ms,
        "session_id": session_id,
        "archive_rows_collapsed": row.archive_rows_collapsed,
    }


def group_by_object_key(
    rows: Iterable[BronzeRow], *, ingested_at_ms: int, session_id: str
) -> dict[str, list[dict[str, Any]]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row_object_key(row)
        envelope_row = to_envelope_row(row, ingested_at_ms=ingested_at_ms, session_id=session_id)
        by_key.setdefault(key, []).append(envelope_row)
    return by_key


def write_parquet_object(s3, bucket: str, key: str, envelope_rows: list[dict[str, Any]]) -> None:
    """Write one coin/dt/hour Parquet object at a deterministic key -- re-invoking the
    same hour overwrites it in place rather than appending (AC4)."""
    table = pa.Table.from_pylist(envelope_rows, schema=SCHEMA)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())


def run(
    date: str,
    hour: str,
    *,
    watchlist: list[str] | None = None,
    bucket: str | None = None,
    s3_client=None,
    session_id: str | None = None,
    ingested_at_ms: int | None = None,
) -> dict[str, int]:
    """Process one archive hour file end to end and write it to bronze. Returns a
    summary (`objects_written`, `rows_written`) for the CloudWatch/CLI report line."""
    watchlist = watchlist if watchlist is not None else load_watchlist()
    s3 = s3_client or boto3.client("s3", region_name=ARCHIVE_REGION)
    bucket = bucket or os.environ["HYPERLAKE_DATA_BUCKET"]
    session_id = session_id or f"backfill-{date}-{hour}"
    ingested_at_ms = ingested_at_ms if ingested_at_ms is not None else int(time.time() * 1000)

    obj = s3.get_object(
        Bucket=ARCHIVE_BUCKET,
        Key=f"{ARCHIVE_PREFIX}/{date}/{hour}.lz4",
        RequestPayer="requester",
    )
    fills = filter_watchlist(iter_fills(read_hour_file(obj["Body"])), watchlist)
    rows = build_rows(collapse_fills(fills), hour=hour)
    by_key = group_by_object_key(rows, ingested_at_ms=ingested_at_ms, session_id=session_id)

    for key, envelope_rows in by_key.items():
        write_parquet_object(s3, bucket, key, envelope_rows)

    return {
        "objects_written": len(by_key),
        "rows_written": sum(len(v) for v in by_key.values()),
    }


def handler(event: dict, context: object = None) -> dict[str, int]:
    # `watchlist.load_watchlist()`'s own default path resolves against a src-layout
    # checkout (see its docstring) -- not valid inside the deployment zip, where
    # config/watchlist.yaml is bundled as a sibling of the `hyperlake` package instead.
    watchlist = None
    task_root = os.environ.get("LAMBDA_TASK_ROOT")
    if task_root:
        watchlist = load_watchlist(os.path.join(task_root, "config", "watchlist.yaml"))
    return run(date=event["date"], hour=str(event["hour"]), watchlist=watchlist)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="archive date, YYYYMMDD")
    parser.add_argument("--hour", required=True, help="archive hour file basename, e.g. 12")
    args = parser.parse_args()
    summary = run(date=args.date, hour=args.hour)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
