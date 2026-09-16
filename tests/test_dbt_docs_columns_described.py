"""QNT-470 AC2: every silver/gold/recon column carries a non-empty description --
proven over `dbt docs generate`'s manifest.json output (ci.yml's "dbt docs generate
(duckdb)" step runs the same command on every push to main).
"""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"

_DESCRIBED_PATH_PREFIXES = ("silver/", "gold/", "recon/")


def _manifest() -> dict:
    subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "dbt",
            "dbt",
            "docs",
            "generate",
            "--profiles-dir",
            ".",
            "--target",
            "duckdb",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
    )
    return json.loads((DBT_DIR / "target" / "manifest.json").read_text())


def test_every_silver_gold_recon_column_has_a_description():
    manifest = _manifest()
    undocumented = []
    checked = 0
    for unique_id, node in manifest["nodes"].items():
        is_described_model = node.get("resource_type") == "model" and node.get(
            "path", ""
        ).startswith(_DESCRIBED_PATH_PREFIXES)
        if not is_described_model:
            continue
        for name, column in node["columns"].items():
            checked += 1
            if not column.get("description", "").strip():
                undocumented.append(f"{unique_id}.{name}")

    assert checked > 0, "no silver/gold/recon columns found -- check the path filter"
    assert undocumented == [], f"missing column description(s): {undocumented}"
