"""Pins drop_seam_schema's prefix guard (QNT-484 AC1): the macro is destructive, so a schema
outside `seam_test_pr*` must be refused before any adapter call. Runs the real
`dbt run-operation` on the duckdb target with no AWS access; the guard raises first, so no
warehouse is touched. The live drop path is covered by the seam-pr runs.
"""

import subprocess
from pathlib import Path

import pytest

DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


def _run_drop(schema: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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


@pytest.mark.parametrize(
    "schema", ["silver", "seam_test", "gold", "seam_test_prod", "seam_test_pr"]
)
def test_drop_seam_schema_refuses_non_pr_schema(schema: str) -> None:
    result = _run_drop(schema)
    assert result.returncode != 0
    assert f"refusing to drop '{schema}'" in result.stdout + result.stderr


def test_drop_seam_schema_accepts_pr_schema() -> None:
    # Positive control: a guard that refused everything would pass the cases above. The schema
    # does not exist on duckdb, so the macro passes the guard and stops at its no-op branch.
    result = _run_drop("seam_test_pr76")
    assert result.returncode == 0
    assert "refusing to drop" not in result.stdout + result.stderr
