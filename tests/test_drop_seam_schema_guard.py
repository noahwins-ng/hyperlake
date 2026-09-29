"""Pins drop_seam_schema's prefix guard (QNT-484 AC1): the macro is destructive, so a schema
outside `seam_test_pr*` must be refused before any adapter call. Runs the real
`dbt run-operation` on the duckdb target with no AWS access; the guard raises first, so no
warehouse is touched. The live drop path is covered by the seam-pr runs.
"""

import subprocess
from pathlib import Path

import pytest

DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


@pytest.mark.parametrize("schema", ["silver", "seam_test", "gold"])
def test_drop_seam_schema_refuses_non_pr_schema(schema: str) -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "dbt",
            "dbt",
            "--no-populate-cache",
            "run-operation",
            "drop_seam_schema",
            "--profiles-dir",
            ".",
            "--target",
            "duckdb",
            "--args",
            f"{{schema: {schema}}}",
        ],
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
    )
    assert result.returncode != 0
    assert f"refusing to drop '{schema}'" in result.stdout + result.stderr
