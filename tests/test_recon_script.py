"""AC3 (code side): `make recon SESSION=` computes the reconcilable window, runs
recon_trades (+tests) via the shared dbt-run workflow (which regenerates the
session_gaps seed itself, see tests/test_regen_recon_seed.py -- a local seed write here
would never reach that remote checkout), and writes the resulting
ws_only/backfill_only/both counts into the manifest's `recon` block. Exercised against
`run_recon` with every side effect stubbed (no real AWS/gh calls) -- mirrors
tests/test_session_down_drain.py's DI pattern. The real `make recon SESSION=<live
session>` round trip needs a live AWS/gh session (AC3's dev-execution half) and is not
run here.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperlake.session import write_manifest
from scripts.recon import RunReconDeps, run_recon


def _manifest(
    tmp_path: Path,
    *,
    end: str | None = "2026-09-08T12:00:00Z",
    session_id: str = "dev-20260908090000",
) -> Path:
    path = tmp_path / "manifest.json"
    write_manifest(
        path,
        {
            "session_id": session_id,
            "coins": ["BTC"],
            "start": "2026-09-08T09:00:00Z",
            "end": end,
            "deployed_sha": "abc123",
            "gaps": [
                {"start": "2026-09-08T10:10:00Z", "end": "2026-09-08T10:15:00Z", "healed": False}
            ],
            "recon": {"ws_only": None, "backfill_only": None, "both": None, "window": None},
            "dbt_runs": [],
            "cost_estimate_usd": 0.5,
            "cost_actual_usd": None,
            "reaped": False,
        },
    )
    return path


def _deps(*, dbt_status: str = "success", counts: dict | None = None) -> tuple[RunReconDeps, list]:
    calls: list = []
    deps = RunReconDeps(
        now=lambda: datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC),
        run_dbt=lambda run_key, dbt_vars: (
            calls.append(f"run_dbt:{run_key}:{json.dumps(dbt_vars, sort_keys=True)}")
            or {"run_key": run_key, "url": "https://x/1", "status": dbt_status}
        ),
        query_counts=lambda session_id: (
            calls.append(f"query_counts:{session_id}")
            or (counts or {"ws_only": 0, "backfill_only": 1, "both": 5})
        ),
    )
    return deps, calls


def test_writes_recon_counts_into_manifest(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, _ = _deps(counts={"ws_only": 0, "backfill_only": 2, "both": 10})

    result = run_recon(manifest_path, deps)

    assert result["recon"] == {
        "ws_only": 0,
        "backfill_only": 2,
        "both": 10,
        "window": "2026-09-08T09:00:00+00:00/2026-09-08T12:00:00+00:00",
    }
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["recon"]["both"] == 10


def test_dbt_vars_carry_the_reconcilable_window_and_session_id(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()

    run_recon(manifest_path, deps)

    run_dbt_call = next(c for c in calls if c.startswith("run_dbt:"))
    assert run_dbt_call.startswith("run_dbt:recon-dev-20260908090000:")
    dbt_vars = json.loads(run_dbt_call.split(":", 2)[2])
    assert dbt_vars == {
        "recon_session_id": "dev-20260908090000",
        "recon_window_start": "2026-09-08 09:00:00",
        "recon_window_end": "2026-09-08 12:00:00",
    }


def test_unsafe_session_id_fails_loud(tmp_path):
    # session_id is interpolated into an Athena query string in _query_athena_counts --
    # reject anything outside the shape session_up.py's LABEL_RE already enforces at
    # session creation, rather than trust it unconditionally.
    manifest_path = _manifest(tmp_path, session_id="dev'; drop table recon_trades; --")
    deps, calls = _deps()

    with pytest.raises(RuntimeError, match="must match"):
        run_recon(manifest_path, deps)
    assert calls == []


def test_no_end_fails_loud(tmp_path):
    manifest_path = _manifest(tmp_path, end=None)
    deps, calls = _deps()

    with pytest.raises(RuntimeError, match="no end"):
        run_recon(manifest_path, deps)
    assert calls == []


def test_no_reconcilable_hour_yet_fails_loud(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()
    deps.now = lambda: datetime(2026, 9, 8, 9, 30, tzinfo=UTC)  # archive still lagging

    with pytest.raises(RuntimeError, match="no reconcilable hour"):
        run_recon(manifest_path, deps)
    assert calls == []


def test_failed_dbt_run_fails_loud_and_does_not_write_recon_block(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, _ = _deps(dbt_status="failed")

    with pytest.raises(RuntimeError, match="dbt-run failed"):
        run_recon(manifest_path, deps)

    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["recon"]["both"] is None
