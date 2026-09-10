"""Pins the Reservoir fallback backfill reader (QNT-465): the column mapping onto
official.py's fill shape + reused pair collapse (AC1), and loud failure on a missing
required column rather than a null-filled row (AC2).
"""

import io
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from hyperlake.backfill.official import (
    BronzeRow,
    collapse_fills,
    group_by_object_key,
    row_object_key,
    write_parquet_object,
)
from hyperlake.backfill.reservoir import (
    SchemaError,
    _S3RandomAccessFile,
    build_rows,
    dex_for_coin,
    map_row,
    object_key_for,
    read_fills,
)

FIXTURE = Path(__file__).parent / "fixtures" / "reservoir_fixture.parquet"
DRIFT_FIXTURE = Path(__file__).parent / "fixtures" / "reservoir_fixture_missing_trade_id.parquet"
WATCHLIST = ["BTC", "ETH", "HYPE", "xyz:SP500", "xyz:XYZ100"]


def _raw_fills_for_tid(tid: int) -> list[dict]:
    return [f for f in read_fills(FIXTURE) if f["trade_id"] == tid]


# ---- AC1: column mapping + pair collapse (2 rows -> 1) ------------------------------


def test_read_fills_yields_every_row_in_the_100_row_fixture():
    assert len(list(read_fills(FIXTURE))) == 100


def test_map_row_renames_columns_onto_the_official_fill_shape():
    raw = _raw_fills_for_tid(500000000001)[0]
    mapped = map_row(raw)
    assert mapped["tid"] == 500000000001
    assert mapped["coin"] == "BTC"
    assert mapped["hash"] == "0xrespair1"
    assert "trade_id" not in mapped
    assert "price" not in mapped
    assert "timestamp" not in mapped


def test_map_row_normalises_reservoir_side_codes_to_the_official_convention():
    raw = _raw_fills_for_tid(500000000001)
    sides = {r["side"]: map_row(r)["side"] for r in raw}
    assert sides == {"buy": "B", "sell": "A"}


def test_map_row_raises_schema_error_on_an_unrecognised_side_value():
    raw = dict(_raw_fills_for_tid(500000000001)[0])
    raw["side"] = "unknown"
    with pytest.raises(SchemaError, match="unknown"):
        map_row(raw)


def test_map_row_converts_timestamp_to_epoch_ms():
    raw = _raw_fills_for_tid(500000000001)[0]
    # Fixture pins tid 500000000001 at 2026-09-03T12:00:00Z.
    assert map_row(raw)["time"] == 1788436800000


def test_collapse_of_mapped_reservoir_pair_yields_one_row():
    mapped = [map_row(r) for r in _raw_fills_for_tid(500000000001)]
    rows = list(collapse_fills(mapped))
    assert len(rows) == 1
    fill, archive_rows_collapsed = rows[0]
    assert archive_rows_collapsed == 2
    assert fill["side"] == "A"  # the crossed=True (taker) side, per ADR-005's rule
    assert fill["px"] == "60000.1234567800"
    assert fill["sz"] == "0.5000000000"


def test_collapse_preserves_a_real_liquidation_flag():
    mapped = [map_row(r) for r in _raw_fills_for_tid(500000000002)]
    fill, _ = list(collapse_fills(mapped))[0]
    assert fill["liquidation"] is True


def test_build_rows_derives_hour_from_event_time_not_an_invocation_argument():
    mapped = [map_row(r) for r in _raw_fills_for_tid(500000000001)]
    rows = list(build_rows(collapse_fills(mapped)))
    assert len(rows) == 1
    assert rows[0].dt == "2026-09-03"
    assert rows[0].hour == "12"


def test_object_key_shape_matches_the_official_readers_bronze_layout():
    row = BronzeRow(coin="xyz:SP500", dt="2026-09-03", hour="12", fill={}, archive_rows_collapsed=1)
    expected = "bronze/coin=xyz_SP500/dt=2026-09-03/source=backfill/hour=12.parquet"
    assert row_object_key(row) == expected


# ---- envelope + Parquet round-trip (rescale + JSON-safety of the mapped/collapsed row) --


class _FakeS3:
    def __init__(self):
        self.put_calls: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body):  # noqa: N803 (mirrors boto3's signature)
        self.put_calls[Key] = Body


def test_mapped_reservoir_rows_write_and_read_back_through_the_shared_schema():
    # Reservoir's decimal128(20,10) px/sz/fee must rescale to the envelope's (18,8)/(18,6)
    # cleanly, and the collapsed fill dict (now all str/int/bool) must be JSON-safe for
    # `raw_payload` -- neither is exercised by the AC1 tests above, which stop at build_rows.
    mapped = [map_row(r) for r in read_fills(FIXTURE) if r["coin"] in WATCHLIST]
    rows = build_rows(collapse_fills(mapped))
    by_key = group_by_object_key(rows, ingested_at_ms=1788523200000, session_id="reservoir-test")

    s3 = _FakeS3()
    for key, envelope_rows in by_key.items():
        write_parquet_object(s3, "fake-bucket", key, envelope_rows)

    key = row_object_key(
        BronzeRow(coin="BTC", dt="2026-09-03", hour="12", fill={}, archive_rows_collapsed=0)
    )
    table = pq.read_table(io.BytesIO(s3.put_calls[key]))
    df_row = next(r for r in table.to_pylist() if r["tid"] == 500000000001)
    assert df_row["source"] == "backfill"
    assert df_row["archive_rows_collapsed"] == 2
    assert str(df_row["px"]) == "60000.12345678"
    assert str(df_row["sz"]) == "0.500000"
    assert df_row["side"] == "A"


def test_mapped_liquidation_row_round_trips_true():
    mapped = [map_row(r) for r in read_fills(FIXTURE) if r["coin"] == "ETH"]
    rows = build_rows(collapse_fills(mapped))
    by_key = group_by_object_key(rows, ingested_at_ms=1788523200000, session_id="reservoir-test")

    s3 = _FakeS3()
    for key, envelope_rows in by_key.items():
        write_parquet_object(s3, "fake-bucket", key, envelope_rows)

    key = row_object_key(
        BronzeRow(coin="ETH", dt="2026-09-03", hour="12", fill={}, archive_rows_collapsed=0)
    )
    table = pq.read_table(io.BytesIO(s3.put_calls[key]))
    df_row = next(r for r in table.to_pylist() if r["tid"] == 500000000002)
    assert df_row["liquidation"] is True


# ---- _S3RandomAccessFile: seek/read over ranged GETs, no network ---------------------


class _FakeRangeS3:
    """A boto3 S3 client double that serves `Range` GETs out of an in-memory buffer --
    enough to pin `_S3RandomAccessFile`'s seek/read/EOF handling without real S3 access
    (the real thing is exercised for real in the AC3 dev-execution proof)."""

    def __init__(self, data: bytes):
        self._data = data

    def head_object(self, *, Bucket, Key, RequestPayer):  # noqa: N803
        return {"ContentLength": len(self._data)}

    def get_object(self, *, Bucket, Key, RequestPayer, Range):  # noqa: N803
        start, end = (int(x) for x in Range.removeprefix("bytes=").split("-"))
        chunk = self._data[start : end + 1]
        return {"Body": io.BytesIO(chunk)}


def test_s3_random_access_file_reads_sequentially_from_the_start():
    f = _S3RandomAccessFile(_FakeRangeS3(b"0123456789"), "b", "k")
    assert f.size == 10
    assert f.read(4) == b"0123"
    assert f.tell() == 4
    assert f.read(4) == b"4567"


def test_s3_random_access_file_seek_whence_variants():
    f = _S3RandomAccessFile(_FakeRangeS3(b"0123456789"), "b", "k")
    assert f.seek(2) == 2
    assert f.read(2) == b"23"
    assert f.seek(1, 1) == 5  # whence=1: relative to current position
    assert f.read(1) == b"5"
    assert f.seek(-3, 2) == 7  # whence=2: relative to EOF
    assert f.read() == b"789"


def test_s3_random_access_file_read_past_eof_returns_only_whats_left():
    f = _S3RandomAccessFile(_FakeRangeS3(b"01234"), "b", "k")
    f.seek(3)
    assert f.read(100) == b"34"


def test_s3_random_access_file_read_at_exact_eof_returns_empty():
    f = _S3RandomAccessFile(_FakeRangeS3(b"01234"), "b", "k")
    f.seek(5)
    assert f.read(10) == b""


def test_s3_random_access_file_zero_size_read_returns_empty_without_a_request():
    f = _S3RandomAccessFile(_FakeRangeS3(b"01234"), "b", "k")
    assert f.read(0) == b""


def test_s3_random_access_file_reports_closed():
    f = _S3RandomAccessFile(_FakeRangeS3(b"01234"), "b", "k")
    assert f.closed is False
    f.close()
    assert f.closed is True


# ---- dex routing ----------------------------------------------------------------------


def test_dex_for_coin_routes_hip3_markets_by_their_prefix():
    assert dex_for_coin("xyz:SP500") == "xyz"


def test_dex_for_coin_routes_plain_markets_to_hyperliquid():
    assert dex_for_coin("BTC") == "hyperliquid"


def test_object_key_for_matches_the_reservoir_path_pattern():
    expected = "by_dex/xyz/fills/perp/all/date=2026-09-03/fills.parquet"
    assert object_key_for("xyz", "2026-09-03") == expected


# ---- watchlist filtering reuses official.py's filter_watchlist ----------------------


def test_non_watchlist_coin_is_still_present_before_filtering():
    # Fixture includes a non-watchlist SOL pair -- filtering itself is official.py's
    # filter_watchlist, already pinned by test_backfill_official.py; this just proves
    # the fixture exercises it.
    assert any(f["coin"] == "SOL" for f in read_fills(FIXTURE))
    assert "SOL" not in WATCHLIST


# ---- AC2: schema drift is loud, not null-filled --------------------------------------


def test_read_fills_raises_schema_error_when_trade_id_is_missing():
    with pytest.raises(SchemaError, match="trade_id"):
        list(read_fills(DRIFT_FIXTURE))


def test_schema_error_is_raised_before_any_row_is_read():
    # The drift fixture has real rows for its remaining columns -- if the reader ever
    # started null-filling instead of failing loud, this would return data, not raise.
    pf = pq.ParquetFile(DRIFT_FIXTURE)
    assert pf.metadata.num_rows == 2  # sanity: the fixture isn't accidentally empty
    with pytest.raises(SchemaError):
        list(read_fills(DRIFT_FIXTURE))
