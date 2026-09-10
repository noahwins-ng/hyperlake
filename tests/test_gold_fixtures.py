"""QNT-463 AC1: proves assert_ohlcv_bounds passes on the committed duckdb fixtures and
fails once a candle row is corrupted. Runs a real `dbt build --target duckdb` (same
command CI runs via `make dbt-build`), matching the subprocess pattern in
test_dbt_silver_merge_config.py / test_recon_fixtures.py. The corruption step shells out
to a `uv run --group dbt python` subprocess (not a direct `import duckdb` in this test
module) for the same reason test_recon_fixtures.py does: duckdb is only in the `dbt`
dependency group, which plain `uv run pytest` (make test) doesn't install.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"
DUCKDB_PATH = "test_gold_fixtures.duckdb"


def _dbt_env() -> dict:
    import os

    return {**os.environ, "DBT_DUCKDB_PATH": DUCKDB_PATH}


def _dbt(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "--group", "dbt", "dbt", *args, "--profiles-dir", ".", "--target", "duckdb"],
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
        env=_dbt_env(),
    )


def _dbt_build() -> subprocess.CompletedProcess:
    return _dbt("build", "--select", "+gold")


def _dbt_test_bounds() -> subprocess.CompletedProcess:
    return _dbt("test", "--select", "assert_ohlcv_bounds")


def _corrupt_one_candle_row() -> None:
    corrupt_sql = (
        "update gold.ohlcv_1m set high = low - 1 "
        "where coin = 'BTC' "
        "and bucket = (select min(bucket) from gold.ohlcv_1m where coin = 'BTC')"
    )
    script = (
        "import duckdb\n"
        f"con = duckdb.connect({DUCKDB_PATH!r})\n"
        f"con.execute({corrupt_sql!r})\n"
        "con.close()\n"
    )
    result = subprocess.run(
        ["uv", "run", "--group", "dbt", "python", "-c", script],
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
        check=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ohlcv_bounds_passes_on_clean_fixture():
    result = _dbt_build()
    assert result.returncode == 0, result.stdout + result.stderr


def test_ohlcv_bounds_fails_on_corrupted_row():
    build = _dbt_build()
    assert build.returncode == 0, build.stdout + build.stderr

    _corrupt_one_candle_row()

    result = _dbt_test_bounds()
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "FAIL 1 assert_ohlcv_bounds" in output
