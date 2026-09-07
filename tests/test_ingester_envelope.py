"""Pins the ingester's envelope builder (QNT-456 AC1): a WS `trades` message and the
backfill reader's archive fill for the *same* `tid` must agree on every field the WS feed
can actually populate, and the archive-only columns (`crossed`, `liquidation`, `fee`,
`archive_rows_collapsed`) must be null on the WS side by construction -- the WS feed
structurally never carries them (PRD Data model, 2026-09-04 spike), so a literal
full-envelope diff would never hold even for real, matching data.
"""

import io

import pyarrow.parquet as pq

from hyperlake.backfill.official import BronzeRow
from hyperlake.backfill.official import to_envelope_row as backfill_envelope_row
from hyperlake.envelope import SCHEMA
from hyperlake.ingester import to_envelope_row as ws_envelope_row

SHARED_FIELDS = ("tid", "coin", "side", "px", "sz", "time", "hash")
ARCHIVE_ONLY_FIELDS = ("crossed", "liquidation", "fee", "archive_rows_collapsed")

# The same real trade, as it appears on each path.
ARCHIVE_FILL = {
    "coin": "BTC",
    "px": "60000.12345678",
    "sz": "0.500000",
    "side": "B",
    "time": 1788519900000,
    "tid": 1001,
    "hash": "0xaaa1",
    "crossed": True,
    "fee": "0.012000",
}
WS_TRADE = {
    "coin": "BTC",
    "side": "B",
    "px": "60000.12345678",
    "sz": "0.500000",
    "time": 1788519900000,
    "hash": "0xaaa1",
    "tid": 1001,
    "users": ["0xbuyer", "0xseller"],
}


def _backfill_envelope() -> dict:
    row = BronzeRow(
        coin=ARCHIVE_FILL["coin"],
        dt="2026-09-04",
        hour="12",
        fill=ARCHIVE_FILL,
        archive_rows_collapsed=1,
    )
    return backfill_envelope_row(row, ingested_at_ms=1788519999000, session_id="backfill-test")


def _ws_envelope() -> dict:
    return ws_envelope_row(WS_TRADE, ingested_at_ms=1788519999000, session_id="ws-test")


def test_shared_fields_agree_between_the_ws_and_backfill_envelope_for_the_same_tid():
    ws_env = _ws_envelope()
    backfill_env = _backfill_envelope()
    for field in SHARED_FIELDS:
        assert ws_env[field] == backfill_env[field], field


def test_ws_envelope_nulls_the_archive_only_fields():
    ws_env = _ws_envelope()
    for field in ARCHIVE_ONLY_FIELDS:
        assert ws_env[field] is None, field


def test_backfill_envelope_populates_the_archive_only_fields_for_the_same_trade():
    # Documents the expected divergence: the two envelopes share a schema, not identical
    # values -- the archive genuinely knows more about this trade than the feed does.
    backfill_env = _backfill_envelope()
    assert backfill_env["crossed"] is True
    assert backfill_env["fee"] is not None
    assert backfill_env["archive_rows_collapsed"] == 1


def test_ws_envelope_has_the_same_key_set_as_the_backfill_envelope():
    assert set(_ws_envelope()) == set(_backfill_envelope())


def test_ws_envelope_round_trips_through_the_shared_parquet_schema():
    table = pq.read_table(_write_parquet(_ws_envelope()))
    row = table.to_pylist()[0]
    assert row["source"] == "ws"
    assert row["crossed"] is None
    assert str(row["px"]) == "60000.12345678"


def _write_parquet(row: dict) -> io.BytesIO:
    import pyarrow as pa

    table = pa.Table.from_pylist([row], schema=SCHEMA)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)
    return buf
