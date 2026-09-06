"""Pins the official-archive backfill reader (QNT-451): the taker-fill collapse rule
(AC1), event-time `dt` vs. invocation-hour object keys (AC2), and true streaming
decode -- never `.read()` the whole S3 body into memory (AC6).
"""

import io
from pathlib import Path

import lz4.frame
import pyarrow.parquet as pq
import pytest

from hyperlake.backfill.official import (
    BronzeRow,
    build_rows,
    collapse_fills,
    filter_watchlist,
    group_by_object_key,
    iter_fills,
    read_hour_file,
    row_object_key,
    write_parquet_object,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hour_file_fixture.jsonl"
WATCHLIST = ["BTC", "ETH", "HYPE", "xyz:SP500", "xyz:XYZ100"]


def _fixture_lz4_bytes() -> bytes:
    return lz4.frame.compress(FIXTURE.read_bytes())


class _StreamOnly:
    """A file-like that only supports sized reads, like `botocore`'s `StreamingBody` --
    an unsized `.read()` (i.e. "read it all into memory") raises."""

    def __init__(self, data: bytes):
        self._buf = data
        self._pos = 0

    def read(self, size: int | None = None) -> bytes:
        if size is None:
            raise AssertionError("reader called .read() with no size -- not streaming")
        chunk = self._buf[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


# ---- AC6: streaming decode ----------------------------------------------------------


def test_read_hour_file_never_reads_the_whole_body_at_once():
    stream = _StreamOnly(_fixture_lz4_bytes())
    fills = list(iter_fills(read_hour_file(stream)))
    assert len(fills) > 0


def test_read_hour_file_yields_every_fill_across_every_watchlist_and_non_watchlist_coin():
    stream = _StreamOnly(_fixture_lz4_bytes())
    fills = list(iter_fills(read_hour_file(stream)))
    # 200 blocks: 199 single-fill + 1 two-fill (the crossed pair) = 201 fill events.
    assert len(fills) == 201


# ---- AC1: collapse rule ---------------------------------------------------------------


def _fills_for_tid(tid: int) -> list[dict]:
    stream = _StreamOnly(_fixture_lz4_bytes())
    return [f for f in iter_fills(read_hour_file(stream)) if f["tid"] == tid]


def test_collapse_keeps_the_crossed_side_of_a_pair_and_records_two_collapsed():
    rows = list(collapse_fills(_fills_for_tid(1001)))
    assert len(rows) == 1
    fill, archive_rows_collapsed = rows[0]
    assert fill["side"] == "B"
    assert fill["crossed"] is True
    assert archive_rows_collapsed == 2


def test_collapse_never_sums_px_or_sz_across_the_pair():
    rows = list(collapse_fills(_fills_for_tid(1001)))
    fill, _ = rows[0]
    assert fill["px"] == "60000.12345678"
    assert fill["sz"] == "0.500000"


def test_collapse_keeps_the_sole_fill_and_records_one_collapsed():
    rows = list(collapse_fills(_fills_for_tid(1002)))
    assert len(rows) == 1
    fill, archive_rows_collapsed = rows[0]
    assert fill["tid"] == 1002
    assert archive_rows_collapsed == 1


def test_watchlist_filter_drops_non_watchlist_coins():
    stream = _StreamOnly(_fixture_lz4_bytes())
    all_fills = list(iter_fills(read_hour_file(stream)))
    kept = list(filter_watchlist(all_fills, WATCHLIST))
    dropped = [f for f in all_fills if f not in kept]
    assert len(dropped) == 1
    assert dropped[0]["tid"] == 2001
    assert all(f["coin"] in WATCHLIST for f in kept)


# ---- AC2: event-time dt vs. invocation-hour object key ------------------------------


def test_boundary_fill_dt_comes_from_event_time_not_file_hour():
    # tid 1003's `time` is 11:59:59.860 on 2026-09-04 (hour 11), inside archive file
    # "12.lz4" (arrival-cut). `dt` must come from that `time`, and `hour` must stay the
    # literal invocation value "12" -- neither may be re-derived from the other.
    rows = list(build_rows(collapse_fills(_fills_for_tid(1003)), hour="12"))
    assert len(rows) == 1
    assert rows[0].dt == "2026-09-04"
    assert rows[0].hour == "12"


def test_object_key_uses_the_invocation_hour_not_the_events_own_hour():
    row = BronzeRow(coin="HYPE", dt="2026-09-04", hour="12", fill={}, archive_rows_collapsed=1)
    key = row_object_key(row)
    assert key == "bronze/coin=HYPE/dt=2026-09-04/source=backfill/hour=12.parquet"


def test_object_key_normalises_hip3_colon_in_coin():
    row = BronzeRow(coin="xyz:SP500", dt="2026-09-04", hour="12", fill={}, archive_rows_collapsed=1)
    key = row_object_key(row)
    assert key == "bronze/coin=xyz_SP500/dt=2026-09-04/source=backfill/hour=12.parquet"


@pytest.mark.parametrize("tid", [1001, 1002, 1003])
def test_every_collapsed_row_keeps_time_from_the_underlying_fill(tid):
    rows = list(collapse_fills(_fills_for_tid(tid)))
    fill, _ = rows[0]
    assert "time" in fill


# ---- envelope + Parquet round-trip (schema compatibility, no S3 needed) -------------


class _FakeS3:
    def __init__(self):
        self.put_calls: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body):  # noqa: N803 (mirrors boto3's signature)
        self.put_calls[Key] = Body


def test_envelope_rows_write_and_read_back_through_the_shared_schema():
    stream = _StreamOnly(_fixture_lz4_bytes())
    fills = filter_watchlist(iter_fills(read_hour_file(stream)), WATCHLIST)
    rows = list(build_rows(collapse_fills(fills), hour="12"))
    by_key = group_by_object_key(rows, ingested_at_ms=1788523200000, session_id="backfill-test")

    s3 = _FakeS3()
    for key, envelope_rows in by_key.items():
        write_parquet_object(s3, "fake-bucket", key, envelope_rows)

    assert len(s3.put_calls) == len(by_key)
    key = row_object_key(
        BronzeRow(coin="BTC", dt="2026-09-04", hour="12", fill={}, archive_rows_collapsed=0)
    )
    table = pq.read_table(io.BytesIO(s3.put_calls[key]))
    df_row = table.to_pylist()[0]
    assert df_row["source"] == "backfill"
    assert df_row["archive_rows_collapsed"] == 2
    assert str(df_row["px"]) == "60000.12345678"


# ---- grain-rule drift guard (ADR-005: a pair always has one crossed=true fill) ------


def test_collapse_raises_loudly_when_a_pair_has_no_crossed_fill():
    # Never observed in the real archive (ADR-005's gate measured 0 such cases), but if
    # the archive format ever changes, silently guessing a side would be worse than
    # failing loudly -- this is the schema-drift signal ADR-005 says the reader must catch.
    pair = [
        {"tid": 9001, "side": "A", "crossed": False},
        {"tid": 9001, "side": "B", "crossed": False},
    ]
    with pytest.raises(ValueError, match="9001"):
        list(collapse_fills(pair))
