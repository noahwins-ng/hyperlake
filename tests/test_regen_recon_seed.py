"""dbt-run.yml's session_gaps-seed pre-step (QNT-460): the runner's own checkout is the
only place that can regenerate dbt/seeds/session_gaps.csv from a real session's
manifest -- a local write on the dispatching machine (scripts/recon.py) never reaches
this remote checkout. Found by actually dispatching against real Athena: the seed the
remote build loaded was the committed fixture content, not the local edit.
"""

import csv
import json
from pathlib import Path

from hyperlake.session import write_manifest
from scripts.regen_recon_seed import regenerate_seed


def _manifest(sessions_dir: Path, session_id: str, gaps: list[dict]) -> None:
    sessions_dir.mkdir(exist_ok=True)
    write_manifest(
        sessions_dir / f"{session_id}.json",
        {
            "session_id": session_id,
            "coins": ["BTC"],
            "start": "2026-09-08T13:00:00Z",
            "end": "2026-09-08T14:00:00Z",
            "deployed_sha": "abc123",
            "gaps": gaps,
            "recon": {"ws_only": None, "backfill_only": None, "both": None, "window": None},
            "dbt_runs": [],
            "cost_estimate_usd": 0.1,
            "cost_actual_usd": None,
            "reaped": False,
        },
    )


def test_writes_the_named_sessions_gaps_into_the_seed(tmp_path):
    sessions_dir = tmp_path / "sessions"
    seed_path = tmp_path / "session_gaps.csv"
    _manifest(
        sessions_dir,
        "qnt-460-verify",
        [{"start": "2026-09-08T13:10:00Z", "end": "2026-09-08T13:15:00Z", "healed": False}],
    )
    dbt_vars = json.dumps({"recon_session_id": "qnt-460-verify"})

    result = regenerate_seed(dbt_vars, sessions_dir, seed_path)

    assert result == "qnt-460-verify"
    rows = list(csv.DictReader(seed_path.open()))
    assert rows == [
        {
            "session_id": "qnt-460-verify",
            "gap_start": "2026-09-08T13:10:00Z",
            "gap_end": "2026-09-08T13:15:00Z",
        }
    ]


def test_noop_when_vars_is_empty(tmp_path):
    sessions_dir = tmp_path / "sessions"
    seed_path = tmp_path / "session_gaps.csv"
    seed_path.write_text("session_id,gap_start,gap_end\nrecon-fixture-gap,x,y\n")

    result = regenerate_seed("", sessions_dir, seed_path)

    assert result is None
    assert "recon-fixture-gap" in seed_path.read_text()


def test_noop_when_vars_has_no_recon_session_id(tmp_path):
    sessions_dir = tmp_path / "sessions"
    seed_path = tmp_path / "session_gaps.csv"
    seed_path.write_text("session_id,gap_start,gap_end\nrecon-fixture-gap,x,y\n")

    result = regenerate_seed(json.dumps({"silver_lookback_days": 2}), sessions_dir, seed_path)

    assert result is None
    assert "recon-fixture-gap" in seed_path.read_text()


def test_noop_when_named_session_has_no_manifest(tmp_path):
    sessions_dir = tmp_path / "sessions"
    seed_path = tmp_path / "session_gaps.csv"
    seed_path.write_text("session_id,gap_start,gap_end\nrecon-fixture-gap,x,y\n")
    dbt_vars = json.dumps({"recon_session_id": "recon-fixture-clean"})

    result = regenerate_seed(dbt_vars, sessions_dir, seed_path)

    assert result is None
    assert "recon-fixture-gap" in seed_path.read_text()


def test_no_gaps_writes_header_only(tmp_path):
    sessions_dir = tmp_path / "sessions"
    seed_path = tmp_path / "session_gaps.csv"
    _manifest(sessions_dir, "qnt-460-verify", [])
    dbt_vars = json.dumps({"recon_session_id": "qnt-460-verify"})

    result = regenerate_seed(dbt_vars, sessions_dir, seed_path)

    assert result == "qnt-460-verify"
    assert list(csv.DictReader(seed_path.open())) == []
