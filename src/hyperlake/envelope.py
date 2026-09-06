"""The ingester-owned bronze envelope (PRD Data model, OQ-7): one schema shared by the
live WebSocket path and the backfill Lambda so both write the identical Parquet layout.
"""

import argparse
import json

import pyarrow as pa

SCHEMA = pa.schema(
    [
        pa.field("tid", pa.int64(), nullable=False),
        pa.field("coin", pa.string(), nullable=False),
        pa.field("side", pa.string(), nullable=False),
        pa.field("px", pa.decimal128(18, 8), nullable=False),
        pa.field("sz", pa.decimal128(18, 6), nullable=False),
        pa.field("time", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("hash", pa.string()),
        pa.field("crossed", pa.bool_()),
        pa.field("liquidation", pa.bool_()),
        pa.field("fee", pa.decimal128(18, 6)),
        pa.field("raw_payload", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("ingested_at", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("session_id", pa.string(), nullable=False),
        pa.field("archive_rows_collapsed", pa.int8()),
    ]
)

_GLUE_TYPE_OVERRIDES = {
    pa.int8(): "tinyint",
    pa.int64(): "bigint",
    pa.bool_(): "boolean",
    pa.string(): "string",
}


def _glue_type(arrow_type: pa.DataType) -> str:
    if pa.types.is_decimal(arrow_type):
        return f"decimal({arrow_type.precision},{arrow_type.scale})"
    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    for candidate, glue_type in _GLUE_TYPE_OVERRIDES.items():
        if arrow_type.equals(candidate):
            return glue_type
    raise ValueError(f"no Glue type mapping for {arrow_type!r}")


def glue_columns() -> list[dict[str, str]]:
    """Glue-catalog column definitions for the envelope, for Firehose/Terraform."""
    return [{"Name": field.name, "Type": _glue_type(field.type)} for field in SCHEMA]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--glue-json",
        action="store_true",
        help="print the Glue-compatible column schema as JSON",
    )
    parser.parse_args()
    print(json.dumps(glue_columns(), indent=2))


if __name__ == "__main__":
    main()
