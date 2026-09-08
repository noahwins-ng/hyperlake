"""AC2 (code): session-down must wait >= 120s between stopping the ingester and
destroying the ephemeral stack -- the Firehose buffer's 60s window plus margin, so
in-flight records land in S3 before the stream disappears. Exercised against
`run_session_down` with every side effect stubbed (no real AWS/terraform/git calls).
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


def _deps() -> tuple[SessionDownDeps, list[str]]:
    calls: list[str] = []
    deps = SessionDownDeps(
        stop_ingester=lambda: calls.append("stop_ingester"),
        sleep=lambda seconds: calls.append(f"sleep:{seconds}"),
        destroy_ephemeral=lambda image_tag: calls.append(f"destroy_ephemeral:{image_tag}"),
        collect_gaps=lambda session_id, start, end: [],
        run_dbt=lambda run_key: {"run_key": run_key, "url": "https://x/1", "status": "success"},
        run_iceberg_maintain=lambda: calls.append("run_iceberg_maintain"),
        git_commit=lambda paths, message: calls.append("git_commit"),
        now=lambda: datetime(2026, 9, 8, 4, 0, 0, tzinfo=UTC),
        append_cost_row=lambda session_id, start_iso, end_iso, cost_estimate: None,
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


def test_malformed_manifest_missing_deployed_sha_fails_loud(tmp_path):
    path = tmp_path / "dev-20260908000000.json"
    write_manifest(path, {"session_id": "dev-20260908000000", "start": "2026-09-08T00:00:00Z"})
    deps, calls = _deps()

    with pytest.raises(RuntimeError, match="deployed_sha"):
        run_session_down(path, deps)
    assert calls == []
