"""AC2/AC3/AC4 (code side): `make heal SESSION=` must abort loud without starting an
execution when any covering hour hasn't landed yet (AC2), otherwise run backfill ->
dbt-run -> recon -> flip every healed gap and commit (AC3), and no-op when there is
nothing unhealed so a second run is a no-op by construction (AC4). Exercised against
`run_heal` with every side effect stubbed (no real AWS/gh/git calls) -- mirrors
tests/test_session_down_drain.py's and tests/test_recon_script.py's DI pattern.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperlake.session import write_manifest
from scripts.heal import HealDeps, run_heal


def _manifest(tmp_path: Path, *, gaps: list[dict] | None = None) -> Path:
    path = tmp_path / "dev-20260908000000.json"
    write_manifest(
        path,
        {
            "session_id": "dev-20260908000000",
            "coins": ["BTC"],
            "start": "2026-09-08T09:00:00Z",
            "end": "2026-09-08T10:00:00Z",
            "deployed_sha": "abc123",
            "gaps": gaps
            if gaps is not None
            else [
                {"start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z", "healed": False}
            ],
            "recon": None,
            "dbt_runs": [],
            "cost_estimate_usd": 0.5,
            "cost_actual_usd": None,
            "reaped": False,
        },
    )
    return path


def _deps(
    *,
    landed: bool = True,
    dbt_status: str = "success",
    recon_raises: bool = False,
) -> tuple[HealDeps, list]:
    calls: list = []

    def start_backfill(hours, session_id):
        calls.append(f"start_backfill:{session_id}:{json.dumps(hours)}")
        return {"results": []}

    def run_dbt(run_key, dbt_vars):
        calls.append(f"run_dbt:{run_key}:{json.dumps(dbt_vars, sort_keys=True)}")
        return {"run_key": run_key, "url": "https://x/1", "status": dbt_status}

    def run_recon(session_id):
        calls.append(f"run_recon:{session_id}")
        if recon_raises:
            raise RuntimeError("recon failed")

    def check_landed(hour):
        calls.append(f"check_landed:{hour['date']}/{hour['hour']}")
        return landed

    deps = HealDeps(
        check_landed=check_landed,
        start_backfill=start_backfill,
        run_dbt=run_dbt,
        run_recon=run_recon,
        git_commit=lambda paths, message: calls.append(f"git_commit:{message}"),
        now=lambda: datetime(2026, 9, 10, 0, 0, 0, tzinfo=UTC),
    )
    return deps, calls


def test_not_landed_hour_aborts_without_starting_backfill(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps(landed=False)

    with pytest.raises(RuntimeError, match="not yet landed"):
        run_heal(manifest_path, deps)

    assert not any(c.startswith("start_backfill") for c in calls)
    assert not any(c.startswith("run_dbt") for c in calls)
    assert not any(c.startswith("run_recon") for c in calls)


def test_happy_path_flips_gap_healed_and_commits(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()

    result = run_heal(manifest_path, deps)

    assert result["gaps"][0]["healed"] is True
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["gaps"][0]["healed"] is True
    assert any(c.startswith("start_backfill") for c in calls)
    assert any(c.startswith("run_dbt") for c in calls)
    assert "run_recon:dev-20260908000000" in calls
    assert any(c.startswith("git_commit") for c in calls)


def test_backfill_covers_the_full_gap_plus_trailing_hour(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()

    run_heal(manifest_path, deps)

    call = next(c for c in calls if c.startswith("start_backfill"))
    hours = json.loads(call.split(":", 2)[2])
    assert hours == [
        {"date": "20260908", "hour": "9"},
        {"date": "20260908", "hour": "10"},
    ]


def test_start_backfill_receives_the_session_id_run_heal_already_validated(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps()

    run_heal(manifest_path, deps)

    call = next(c for c in calls if c.startswith("start_backfill"))
    assert call.split(":", 2)[1] == "dev-20260908000000"


def test_unsafe_session_id_fails_loud_before_starting_backfill(tmp_path):
    # session_id flows into a shell-executed terraform command (main()'s
    # start_backfill -> _tf_ephemeral_vars -> TF_ARGS) -- reject anything outside the
    # charset session_up.py's LABEL_RE already enforces at session creation, same as
    # scripts/recon.py's SESSION_ID_RE guard on the Athena query string.
    path = tmp_path / "manifest.json"
    write_manifest(
        path,
        {
            "session_id": "dev'; rm -rf /; --",
            "coins": ["BTC"],
            "start": "2026-09-08T09:00:00Z",
            "end": "2026-09-08T10:00:00Z",
            "deployed_sha": "abc123",
            "gaps": [
                {"start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z", "healed": False}
            ],
            "recon": None,
            "dbt_runs": [],
            "cost_estimate_usd": 0.5,
            "cost_actual_usd": None,
            "reaped": False,
        },
    )
    deps, calls = _deps()

    with pytest.raises(RuntimeError, match="must match"):
        run_heal(path, deps)
    assert calls == []


def test_dbt_run_gets_lookback_sized_to_oldest_unhealed_gap(tmp_path):
    manifest_path = _manifest(
        tmp_path,
        gaps=[{"start": "2026-09-05T09:00:00Z", "end": "2026-09-05T10:00:00Z", "healed": False}],
    )
    deps, calls = _deps()

    run_heal(manifest_path, deps)

    call = next(c for c in calls if c.startswith("run_dbt"))
    dbt_vars = json.loads(call.split(":", 2)[2])
    assert dbt_vars == {
        "silver_lookback_days": 6,  # 2026-09-10 - 2026-09-05 + 1
        "freshness_window_start": "2026-09-08 09:00:00",
        "freshness_window_end": "2026-09-08 10:00:00",
    }


def test_failed_dbt_run_does_not_flip_healed_or_run_recon(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps(dbt_status="failed")

    with pytest.raises(RuntimeError, match="dbt-run failed"):
        run_heal(manifest_path, deps)

    assert not any(c.startswith("run_recon") for c in calls)
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["gaps"][0]["healed"] is False


def test_recon_failure_does_not_flip_healed(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, _ = _deps(recon_raises=True)

    with pytest.raises(RuntimeError, match="recon failed"):
        run_heal(manifest_path, deps)

    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["gaps"][0]["healed"] is False


def test_recon_failure_still_persists_the_dbt_run_attempt(tmp_path):
    manifest_path = _manifest(tmp_path)
    deps, calls = _deps(recon_raises=True)

    with pytest.raises(RuntimeError, match="recon failed"):
        run_heal(manifest_path, deps)

    on_disk = json.loads(manifest_path.read_text())
    assert len(on_disk["dbt_runs"]) == 1
    assert on_disk["dbt_runs"][0]["status"] == "success"
    assert any(c.startswith("git_commit") for c in calls)


def test_no_unhealed_gaps_is_a_noop(tmp_path):
    manifest_path = _manifest(
        tmp_path,
        gaps=[{"start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z", "healed": True}],
    )
    deps, calls = _deps()

    result = run_heal(manifest_path, deps)

    assert result["gaps"][0]["healed"] is True
    assert calls == []


def test_malformed_gap_fails_loud_with_a_clean_message(tmp_path):
    manifest_path = _manifest(
        tmp_path,
        gaps=[{"start": "2026-09-08T10:00:00Z", "end": "2026-09-08T10:00:00Z", "healed": False}],
    )
    deps, calls = _deps()

    with pytest.raises(RuntimeError, match="malformed gap"):
        run_heal(manifest_path, deps)
    assert calls == []


def test_already_healed_gaps_stay_healed_alongside_newly_healed_ones(tmp_path):
    manifest_path = _manifest(
        tmp_path,
        gaps=[
            {"start": "2026-09-01T09:00:00Z", "end": "2026-09-01T10:00:00Z", "healed": True},
            {"start": "2026-09-08T09:00:00Z", "end": "2026-09-08T10:00:00Z", "healed": False},
        ],
    )
    deps, _ = _deps()

    result = run_heal(manifest_path, deps)

    assert all(g["healed"] is True for g in result["gaps"])
