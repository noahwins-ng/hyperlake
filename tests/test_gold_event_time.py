"""QNT-463 AC2: gold buckets on silver `time` only (NFR-5) -- `ingested_at` must never
appear in a gold model or the shared ohlcv_candles macro, on either target's compiled SQL.
"""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"
GOLD_MODELS_DIR = DBT_DIR / "models" / "gold"


def _dbt_compile(target: str) -> None:
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
            "--select",
            "gold",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
    )


def test_no_ingested_at_in_gold_model_source():
    for sql_file in GOLD_MODELS_DIR.glob("*.sql"):
        assert "ingested_at" not in sql_file.read_text(), sql_file


def test_no_ingested_at_in_compiled_gold_sql():
    for target in ("duckdb", "athena"):
        _dbt_compile(target)
        manifest = json.loads((DBT_DIR / "target" / "manifest.json").read_text())
        gold_nodes = {
            unique_id: node
            for unique_id, node in manifest["nodes"].items()
            if node["resource_type"] == "model" and node["path"].startswith("gold/")
        }
        assert gold_nodes, "expected gold models in the manifest"
        for unique_id, node in gold_nodes.items():
            assert "ingested_at" not in node["compiled_code"], unique_id
