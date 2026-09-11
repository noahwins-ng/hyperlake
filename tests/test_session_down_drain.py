"""AC2 (code): session-down must wait >= 120s between stopping the ingester and
destroying the ephemeral stack -- the Firehose buffer's 60s window plus margin, so
in-flight records land in S3 before the stream disappears. Exercised against
`run_session_down` with every side effect stubbed (no real AWS/terraform/git calls).

Also covers the reaped path (QNT-459 AC2/AC6): when the reaper's S3 marker is present,
session-down must skip the stop/drain steps (the reaper already did them), print a loud
warning naming the session and `reaped_at`, use `reaped_at` as the session's `end`, and
mark the appended costs row `reaper-terminated` instead of `pending`.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperlake.session import write_manifest
from scripts.session_down import DRAIN_SECONDS, SessionDownDeps, run_session_down


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "dev-20260908000000.json"
    write_manifest(
        path,
        {
            "session_id": "dev-20260908000000",
            "coins": ["BTC"],
            "start": "2026-09-08T00:00:00Z",
            "end": None,
            "deployed_sha": "abc123",
            "gaps": [],
            "recon": None,
            "dbt_runs": [],
            "cost_estimate_usd": None,
            "cost_actual_usd": None,
            "reaped": False,
        },
    )
    return path


def _deps(*, reap_marker: dict | None = None) -> tuple[SessionDownDeps, list[str]]:
    calls: list[str] = []

    def run_dbt(run_key, dbt_vars):
        calls.append(f"run_dbt:{run_key}:{json.dumps(dbt_vars, sort_keys=True)}")
        return {"run_key": run_key, "url": "https://x/1", "status": "success"}

    deps = SessionDownDeps(
        stop_ingester=lambda: calls.append("stop_ingester"),
        sleep=lambda seconds: calls.append(f"sleep:{seconds}"),
        destroy_ephemeral=(
            lambda image_tag, session_id, session_start: calls.append(
                f"destroy_ephemeral:{image_tag}"
            )
        ),
        collect_gaps=lambda session_id, start, end: [],
        run_dbt=run_dbt,
        run_iceberg_maintain=lambda: calls.append("run_iceberg_maintain"),
        git_commit=lambda paths, message: calls.append(f"git_commit:{message}"),
        now=lambda: datetime(2026, 9, 8, 4, 0, 0, tzinfo=UTC),
        append_cost_row=(
            lambda session_id, start_iso, end_iso, cost_estimate, reaped=False: calls.append(
                f"append_cost_row:reaped={reaped}"
            )
        ),
        check_reaped=lambda session_id: reap_marker,
    )
    return deps, calls


def test_waits_at_least_drain_seconds_between_stop_and_destroy(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()

    run_session_down(manifest_path, deps)

    assert calls.index("stop_ingester") < calls.index(f"sleep:{DRAIN_SECONDS}")
    assert calls.index(f"sleep:{DRAIN_SECONDS}") < calls.index("destroy_ephemeral:abc123")
    assert DRAIN_SECONDS >= 120


def test_finalized_manifest_has_end_and_cost_estimate(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, _ = _deps()

    result = run_session_down(manifest_path, deps)

    assert result["end"] == "2026-09-08T04:00:00Z"
    assert result["cost_estimate_usd"] > 0
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["end"] == "2026-09-08T04:00:00Z"


def test_dbt_run_gets_the_real_session_freshness_window(tmp_path):
    # QNT-466: without these, assert_silver_freshness checks dbt_project.yml's
    # placeholder demo-fixture window instead of this session's real one and fails
    # regardless of actual freshness -- pin the exact vars passed, not just that
    # run_dbt was called (found in review: this fix was previously unpinned).
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()

    run_session_down(manifest_path, deps)

    call = next(c for c in calls if c.startswith("run_dbt"))
    dbt_vars = json.loads(call.split(":", 2)[2])
    assert dbt_vars == {
        "freshness_window_start": "2026-09-08 00:00:00",
        "freshness_window_end": "2026-09-08 04:00:00",
    }


def test_malformed_manifest_missing_deployed_sha_fails_loud(tmp_path):
    path = tmp_path / "dev-20260908000000.json"
    write_manifest(path, {"session_id": "dev-20260908000000", "start": "2026-09-08T00:00:00Z"})
    deps, calls = _deps()

    with pytest.raises(RuntimeError, match="deployed_sha"):
        run_session_down(path, deps)
    assert calls == []


def test_reaped_session_skips_stop_and_sleep(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps(
        reap_marker={
            "session_id": "dev-20260908000000",
            "reaped": True,
            "reaped_at": "2026-09-08T00:06:00Z",
        }
    )

    run_session_down(manifest_path, deps)

    assert "stop_ingester" not in calls
    assert f"sleep:{DRAIN_SECONDS}" not in calls
    assert "destroy_ephemeral:abc123" in calls


def test_reaped_session_uses_reaped_at_as_end(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, _ = _deps(
        reap_marker={
            "session_id": "dev-20260908000000",
            "reaped": True,
            "reaped_at": "2026-09-08T00:06:00Z",
        }
    )

    result = run_session_down(manifest_path, deps)

    assert result["end"] == "2026-09-08T00:06:00Z"
    assert result["reaped"] is True
    assert result["reaped_at"] == "2026-09-08T00:06:00Z"


def test_reaped_session_prints_warning_naming_session_and_reaped_at(tmp_path, capsys):
    manifest_path = _manifest(tmp_path)
    deps, _ = _deps(
        reap_marker={
            "session_id": "dev-20260908000000",
            "reaped": True,
            "reaped_at": "2026-09-08T00:06:00Z",
        }
    )

    run_session_down(manifest_path, deps)

    err = capsys.readouterr().err
    assert "dev-20260908000000" in err
    assert "2026-09-08T00:06:00Z" in err


def test_reaped_session_cost_row_marked_reaper_terminated(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps(
        reap_marker={
            "session_id": "dev-20260908000000",
            "reaped": True,
            "reaped_at": "2026-09-08T00:06:00Z",
        }
    )

    run_session_down(manifest_path, deps)

    assert "append_cost_row:reaped=True" in calls


def test_non_reaped_session_cost_row_marked_pending(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()

    run_session_down(manifest_path, deps)

    assert "append_cost_row:reaped=False" in calls
