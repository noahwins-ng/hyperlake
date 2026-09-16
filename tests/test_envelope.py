import json
import subprocess
import sys

import pyarrow as pa

from hyperlake.envelope import SCHEMA, glue_columns

EXPECTED_FIELDS = {
    "tid",
    "coin",
    "side",
    "px",
    "sz",
    "time",
    "hash",
    "crossed",
    "liquidation",
    "fee",
    "raw_payload",
    "source",
    "ingested_at",
    "session_id",
    "archive_rows_collapsed",
}


def test_schema_pins_the_full_field_set():
    assert set(SCHEMA.names) == EXPECTED_FIELDS


def test_schema_pins_decimal_precisions():
    px_type = SCHEMA.field("px").type
    sz_type = SCHEMA.field("sz").type
    fee_type = SCHEMA.field("fee").type
    assert (px_type.precision, px_type.scale) == (18, 8)
    assert (sz_type.precision, sz_type.scale) == (18, 6)
    assert (fee_type.precision, fee_type.scale) == (18, 6)


def test_schema_ws_optional_fields_are_nullable():
    # The WS `trades` feed carries no liquidation/crossed/fee, and archive_rows_collapsed
    # only applies to backfill rows, these must stay nullable so source drift degrades to
    # null instead of a dropped record (PRD Data model).
    for name in ("crossed", "liquidation", "fee", "archive_rows_collapsed"):
        assert SCHEMA.field(name).nullable, f"{name} must be nullable for WS-sourced rows"


def test_schema_identity_fields_are_not_nullable():
    for name in ("tid", "coin", "side", "px", "sz", "time", "source"):
        assert not SCHEMA.field(name).nullable, f"{name} must be required on every row"


def test_schema_time_is_utc_timestamp():
    time_type = SCHEMA.field("time").type
    assert pa.types.is_timestamp(time_type)
    assert time_type.tz == "UTC"


def test_glue_columns_cover_every_field_with_a_glue_type():
    columns = glue_columns()
    names = {c["Name"] for c in columns}
    assert names == EXPECTED_FIELDS
    by_name = {c["Name"]: c["Type"] for c in columns}
    assert by_name["px"] == "decimal(18,8)"
    assert by_name["sz"] == "decimal(18,6)"
    assert by_name["time"] == "timestamp"
    assert by_name["tid"] == "bigint"
    assert by_name["crossed"] == "boolean"


def test_glue_json_cli_prints_valid_json_column_list():
    result = subprocess.run(
        [sys.executable, "-m", "hyperlake.envelope", "--glue-json"],
        capture_output=True,
        text=True,
        check=True,
    )
    columns = json.loads(result.stdout)
    assert {c["Name"] for c in columns} == EXPECTED_FIELDS
