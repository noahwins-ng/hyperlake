"""QNT-464 AC1: silver `trades` has an enforced model contract with every column typed --
proven over `dbt compile`/`manifest.json` output (the dev-execution proof that a real
`dbt build --target athena` run rejects a type-violating fixture lives in the PR, not here;
this pins the config that makes that enforcement possible in the first place).
"""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"

# Every column `silver_trades_select` returns, per the envelope (src/hyperlake/envelope.py)
# plus the two derived lineage columns -- contract enforcement requires every one declared.
# `time` differs per target: duckdb keeps the envelope's tz-aware Parquet timestamp as
# `TIMESTAMP WITH TIME ZONE`; Athena/Hive has no tz-aware timestamp (Glue column is plain
# `timestamp`, per envelope.py's `_glue_type`).
_BASE_COLUMN_TYPES = {
    "tid": "bigint",
    "coin": "varchar",
    "side": "varchar",
    "px": "decimal(18,8)",
    "sz": "decimal(18,6)",
    "hash": "varchar",
    "crossed": "boolean",
    "liquidation": "boolean",
    "fee": "decimal(18,6)",
    "source": "varchar",
    "source_rank": "integer",
    "first_seen_source": "varchar",
}
EXPECTED_COLUMN_TYPES = {
    "duckdb": {**_BASE_COLUMN_TYPES, "time": "timestamp with time zone"},
    "athena": {**_BASE_COLUMN_TYPES, "time": "timestamp"},
}


def _silver_trades_node(target: str) -> dict:
    # `--no-populate-cache` skips dbt-athena's relation-cache warm-up (an `sts:GetCallerIdentity`
    # call) -- CI has zero AWS credentials (CLAUDE.md Environment), matching
    # test_dbt_silver_merge_config.py's `_dbt` helper.
    subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "dbt",
            "dbt",
            "--no-populate-cache",
            "compile",
            "--profiles-dir",
            ".",
            "--target",
            target,
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
    )
    manifest = json.loads((DBT_DIR / "target" / "manifest.json").read_text())
    return manifest["nodes"]["model.hyperlake.trades"]


def test_contract_enforced_on_both_targets():
    for target in ("duckdb", "athena"):
        node = _silver_trades_node(target)
        assert node["contract"]["enforced"] is True


def test_on_schema_change_fails_loud_on_a_dropped_or_retyped_column():
    node = _silver_trades_node("athena")
    assert node["config"]["on_schema_change"] == "fail"


def test_every_returned_column_is_declared_with_its_type():
    for target in ("duckdb", "athena"):
        node = _silver_trades_node(target)
        actual = {name: col["data_type"] for name, col in node["columns"].items()}
        assert actual == EXPECTED_COLUMN_TYPES[target]
