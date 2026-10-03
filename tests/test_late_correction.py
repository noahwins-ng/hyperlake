"""QNT-488 AC2: a late archive correction reaches gold. Builds silver and ohlcv_1m twice on
duckdb: once over the committed bronze fixture, where tid 1002 (ETH) exists only as a feed
row at px 2500.5, then over the same fixture plus an archive row for tid 1002 at px 2501
ingested an hour later. ADR-005's source_rank must make silver carry the archive price and
the trade's minute candle follow it. duckdb is only in the `dbt` dependency group, so the
fixture write and the reads shell out to `uv run --group dbt python`, as
test_gold_fixtures.py does.
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"
DUCKDB_PATH = "test_late_correction.duckdb"
FIXTURE = "fixtures/bronze_trades_sample.parquet"


def _python(script: str) -> str:
    result = subprocess.run(
        ["uv", "run", "--group", "dbt", "python", "-c", script],
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _write_corrected_fixture(path: Path) -> None:
    sql = (
        f"copy (select * from '{FIXTURE}' union all "
        "select * replace (cast(2501 as decimal(18, 8)) as px, 'backfill' as source, "
        "ingested_at + interval 1 hour as ingested_at) "
        f"from '{FIXTURE}' where tid = 1002 and source = 'ws') "
        f"to '{path}' (format parquet)"
    )
    _python(f"import duckdb\nduckdb.connect().execute({sql!r})\n")


def _build(bronze_fixture: str) -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "dbt",
            "dbt",
            "run",
            "--profiles-dir",
            ".",
            "--target",
            "duckdb",
            "--select",
            "+ohlcv_1m",
            "--vars",
            json.dumps({"bronze_fixture": bronze_fixture}),
        ],
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
        env={**os.environ, "DBT_DUCKDB_PATH": DUCKDB_PATH},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _silver_px_and_candle_close() -> tuple[str, str]:
    script = (
        "import duckdb\n"
        f"con = duckdb.connect({DUCKDB_PATH!r}, read_only=True)\n"
        "px = con.execute('select px from main.trades where tid = 1002').fetchone()[0]\n"
        "close = con.execute(\n"
        "    \"select close from gold.ohlcv_1m where coin = 'ETH' \"\n"
        "    \"and bucket = timestamptz '2026-01-01 00:05:00+00'\"\n"
        ").fetchone()[0]\n"
        "print(px, close)\n"
    )
    px, close = _python(script).split()
    return px, close


def test_archive_correction_changes_silver_and_candle(tmp_path):
    _build(FIXTURE)
    assert _silver_px_and_candle_close() == ("2500.50000000", "2500.50000000")

    corrected = tmp_path / "bronze_corrected.parquet"
    _write_corrected_fixture(corrected)
    _build(str(corrected))
    assert _silver_px_and_candle_close() == ("2501.00000000", "2501.00000000")
