"""Guards the Athena table-data location (QNT-482 AC1). dbt-athena defaults table data to
`<s3_staging_dir>/tables/`, and the data bucket expires `athena-results/` after 7 days, so an
unset or mis-set `s3_data_dir` silently deletes silver/gold a week after every write.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_athena_data_dir.sh"


def test_athena_profile_reads_table_data_dir_from_its_own_env_var() -> None:
    profile = yaml.safe_load((REPO_ROOT / "dbt" / "profiles.yml").read_text())
    athena = profile["hyperlake"]["outputs"]["athena"]
    assert "env_var('DBT_ATHENA_S3_DATA_DIR'" in athena["s3_data_dir"]
    assert "athena-results" not in athena["s3_data_dir"]


def test_every_dbt_run_job_that_sets_the_staging_dir_sets_the_data_dir() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "dbt-run.yml").read_text()
    staging = workflow.count("DBT_ATHENA_S3_STAGING_DIR: ${{ vars.DBT_ATHENA_S3_STAGING_DIR }}")
    data = workflow.count("DBT_ATHENA_S3_DATA_DIR: ${{ vars.DBT_ATHENA_S3_DATA_DIR }}")
    assert staging > 0
    assert data == staging
    assert workflow.count("check_athena_data_dir.sh") == staging


def _check(value: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DBT_ATHENA_S3_DATA_DIR"}
    if value is not None:
        env["DBT_ATHENA_S3_DATA_DIR"] = value
    return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "s3://hyperlake-data-x/athena-results/",
        "s3://hyperlake-data-x/athena-results/tables/",
    ],
)
def test_check_refuses_a_missing_or_expiring_data_dir(value: str | None) -> None:
    assert _check(value).returncode == 1


def test_check_accepts_the_warehouse_prefix() -> None:
    result = _check("s3://hyperlake-data-x/warehouse/")
    assert result.returncode == 0
    assert "warehouse/" in result.stdout
