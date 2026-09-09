"""AC1 (fixtures) + AC4 (distinct-tid): proves G3's two singular tests over the three
committed recon fixture scenarios (dbt/fixtures/bronze_trades_sample.parquet,
dbt/seeds/session_gaps.csv) -- clean and legitimate-gap both pass, the real-miss
scenario fails `assert_backfill_only_within_gaps` specifically (not `assert_ws_only_zero`),
and a duplicated live-path row does not inflate `recon_trades`' distinct-tid grain.
Runs a real `dbt build --target duckdb` per scenario (same command CI runs via
`make dbt-build`), matching the subprocess pattern in test_dbt_silver_merge_config.py.
"""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"

SCENARIOS = {
    "recon-fixture-clean": ("2026-02-01 00:00:00", "2026-02-01 01:00:00"),
    "recon-fixture-gap": ("2026-02-01 02:00:00", "2026-02-01 03:00:00"),
    "recon-fixture-miss": ("2026-02-01 04:00:00", "2026-02-01 05:00:00"),
}


def _dbt_build(session_id: str, window_start: str, window_end: str) -> subprocess.CompletedProcess:
    dbt_vars = json.dumps(
        {
            "recon_session_id": session_id,
            "recon_window_start": window_start,
            "recon_window_end": window_end,
        }
    )
    return subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "dbt",
            "dbt",
            "build",
            "--profiles-dir",
            ".",
            "--target",
            "duckdb",
            "--select",
            "recon_trades",
            "session_gaps",
            "--vars",
            dbt_vars,
        ],
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
    )


def test_clean_scenario_passes_both_g3_tests():
    result = _dbt_build("recon-fixture-clean", *SCENARIOS["recon-fixture-clean"])
    assert result.returncode == 0, result.stdout + result.stderr


def test_legitimate_gap_scenario_passes_both_g3_tests():
    result = _dbt_build("recon-fixture-gap", *SCENARIOS["recon-fixture-gap"])
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_miss_scenario_fails_only_the_gap_test():
    result = _dbt_build("recon-fixture-miss", *SCENARIOS["recon-fixture-miss"])
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "FAIL 1 assert_backfill_only_within_gaps" in output
    assert "assert_ws_only_zero" in output
    assert "FAIL" not in output.split("assert_ws_only_zero")[1].split("\n")[0]


def test_duplicated_ws_row_does_not_inflate_distinct_tid_count():
    # tid 2002 (recon-fixture-clean) has two physical `ws`-source bronze rows -- the
    # live path's own retry/duplicate-delivery behavior -- plus one `backfill` row.
    result = _dbt_build("recon-fixture-clean", *SCENARIOS["recon-fixture-clean"])
    assert result.returncode == 0, result.stdout + result.stderr

    # `dbt show` (not a raw duckdb import -- duckdb is only in the `dbt` dependency
    # group, which `make types`/`make test` don't install; CI caught this) reuses the
    # same `uv run --group dbt` subprocess path as `_dbt_build` above.
    show = subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "dbt",
            "dbt",
            "show",
            "--profiles-dir",
            ".",
            "--target",
            "duckdb",
            "--inline",
            "select membership from recon_trades where tid = 2002",
            "--output",
            "json",
            "--limit",
            "-1",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        cwd=DBT_DIR,
        check=True,
    )
    rows = json.loads(show.stdout)["show"]

    assert rows == [{"membership": "both"}]
