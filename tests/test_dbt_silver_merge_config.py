"""Pins the dbt-athena merge config for silver.trades (QNT-453 AC4, AC6): the
materialization_for_target macro's `kind` seam, and the compiled model's source-precedence
merge config, proven over `dbt compile`/`manifest.json` output rather than by inspection.
"""

import ast
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"


def _dbt(command: str, *args: str) -> str:
    # Run with cwd=dbt/, matching `make dbt-build` — profiles.yml's duckdb path is relative
    # and must resolve against the dbt project dir, not the repo root. `--no-populate-cache`
    # skips dbt-athena's relation-cache warm-up, which otherwise calls `sts:GetCallerIdentity`
    # even for a plain `compile` — CI has zero AWS credentials (CLAUDE.md Environment).
    result = subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "dbt",
            "dbt",
            "--no-populate-cache",
            command,
            "--profiles-dir",
            ".",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
    )
    return result.stdout


def _inline_config(target: str, inline: str) -> dict:
    stdout = _dbt("compile", "--target", target, "--inline", inline)
    after = stdout.split("Compiled inline node is:")[-1]
    rendered = after.strip().splitlines()[0]
    return ast.literal_eval(rendered)


def _silver_trades_config(target: str) -> dict:
    _dbt("compile", "--target", target)
    manifest = json.loads((DBT_DIR / "target" / "manifest.json").read_text())
    return manifest["nodes"]["model.hyperlake.trades"]["config"]


def test_kind_table_compiles_to_table_on_both_targets():
    for target in ("duckdb", "athena"):
        config = _inline_config(target, "{{ materialization_for_target(kind='table') }}")
        assert config["materialized"] == "table"


def test_kind_merge_compiles_to_incremental_iceberg_merge_on_athena_only():
    duckdb_config = _inline_config(
        "duckdb", "{{ materialization_for_target(kind='merge', unique_key='tid') }}"
    )
    assert duckdb_config == {"materialized": "table"}

    athena_config = _inline_config(
        "athena", "{{ materialization_for_target(kind='merge', unique_key='tid') }}"
    )
    assert athena_config["materialized"] == "incremental"
    assert athena_config["incremental_strategy"] == "merge"
    assert athena_config["table_type"] == "iceberg"


def test_first_seen_source_excluded_from_merge_update_columns():
    config = _silver_trades_config("athena")
    assert config["merge_exclude_columns"] == ["first_seen_source"]


def test_source_rank_gates_the_matched_update():
    config = _silver_trades_config("athena")
    assert config["update_condition"] == "src.source_rank >= target.source_rank"
