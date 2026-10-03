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
from scripts.regen_recon_seed import append_all_session_gaps, regenerate_seed


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
        [
            {
                "coin": "BTC",
                "start": "2026-09-08T13:10:00Z",
                "end": "2026-09-08T13:15:00Z",
                "healed": False,
            },
            # pre-QNT-480 manifests carry no coin: an empty cell loads as null in dbt,
            # which assert_backfill_only_within_gaps treats as covering every coin
            {"start": "2026-09-08T13:20:00Z", "end": "2026-09-08T13:25:00Z", "healed": False},
        ],
    )
    dbt_vars = json.dumps({"recon_session_id": "qnt-460-verify"})

    result = regenerate_seed(dbt_vars, sessions_dir, seed_path)

    assert result == "qnt-460-verify"
    rows = list(csv.DictReader(seed_path.open()))
    assert rows == [
        {
            "session_id": "qnt-460-verify",
            "coin": "BTC",
            "gap_start": "2026-09-08T13:10:00Z",
            "gap_end": "2026-09-08T13:15:00Z",
        },
        {
            "session_id": "qnt-460-verify",
            "coin": "",
            "gap_start": "2026-09-08T13:20:00Z",
            "gap_end": "2026-09-08T13:25:00Z",
        },
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


def test_hip3_gap_coin_is_written_as_its_partition_value(tmp_path):
    # Athena's bronze.trades_raw has `coin` only as the Hive partition key, so recon_trades
    # sees `xyz_SP500`; a seed row keeping the manifest's `xyz:SP500` matched nothing
    # (2026-09-29 live session: 108 HIP-3 archive-only trades inside their gap flagged).
    sessions_dir = tmp_path / "sessions"
    seed_path = tmp_path / "session_gaps.csv"
    _manifest(
        sessions_dir,
        "qnt-480-verify",
        [
            {
                "coin": "xyz:SP500",
                "start": "2026-09-29T13:32:06Z",
                "end": "2026-09-29T13:32:42Z",
                "healed": False,
            }
        ],
    )

    regenerate_seed(json.dumps({"recon_session_id": "qnt-480-verify"}), sessions_dir, seed_path)

    assert [r["coin"] for r in csv.DictReader(seed_path.open())] == ["xyz_SP500"]


def test_non_recon_build_appends_every_sessions_gaps_to_the_fixture_seed(tmp_path):
    # dq_daily reports gap minutes for every session day, so a plain dbt-run must load every
    # committed manifest's gaps, not only the fixture rows a recon dispatch would replace.
    sessions_dir = tmp_path / "sessions"
    seed_path = tmp_path / "session_gaps.csv"
    seed_path.write_text("session_id,coin,gap_start,gap_end\nrecon-fixture-gap,,x,y\n")
    _manifest(
        sessions_dir,
        "s-a",
        [{"coin": "xyz:SP500", "start": "2026-09-29T13:32:06Z", "end": "2026-09-29T13:32:42Z"}],
    )
    _manifest(
        sessions_dir, "s-b", [{"start": "2026-09-11T10:00:00Z", "end": "2026-09-11T10:05:00Z"}]
    )
    _manifest(sessions_dir, "s-c", [])

    appended = append_all_session_gaps(sessions_dir, seed_path)

    assert appended == 2
    rows = list(csv.DictReader(seed_path.open()))
    assert [(r["session_id"], r["coin"], r["gap_start"]) for r in rows] == [
        ("recon-fixture-gap", "", "x"),
        ("s-a", "xyz_SP500", "2026-09-29T13:32:06Z"),
        ("s-b", "", "2026-09-11T10:00:00Z"),
    ]
