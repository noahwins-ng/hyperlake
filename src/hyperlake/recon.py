"""Reconciliation window + gap-seed helpers for `make recon` (QNT-460, FR-8/ADR-003)."""

import csv
from datetime import datetime, timedelta
from pathlib import Path

# PRD FR-8 / ADR-003: an hour's archive file lands ~1h after the hour ends.
ARCHIVE_LAG_HOURS = 1.0


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _ceil_hour(dt: datetime) -> datetime:
    floor = _floor_hour(dt)
    return floor if floor == dt else floor + timedelta(hours=1)


def reconcilable_window(
    session_start: datetime,
    session_end: datetime,
    now: datetime,
    archive_lag_hours: float = ARCHIVE_LAG_HOURS,
) -> tuple[datetime, datetime] | None:
    """The reconcilable window (ADR-003): session hours whose archive hour file has
    landed, with the session's leading AND trailing partial hours excluded. The
    trailing partial hour is ADR-003's explicit case (its archive file hasn't landed,
    or even been cut, yet -- `ws_only` would be nonzero by construction). The same
    reasoning applies to the leading partial hour: the WS ingester wasn't running yet
    for the sliver of its start hour before `session_start`, so every archive trade in
    that sliver would wrongly show as `backfill_only` outside any recorded gap (found
    2026-09-09 running a real session's window against real Athena data -- ws_only
    correctly hit 0, but backfill_only_within_gaps failed on exactly this sliver).

    Returns `(window_start, window_end)` at hour granularity (`window_end` exclusive),
    or `None` if no hour in the session is both fully covered and landed yet.
    """
    window_start = _ceil_hour(session_start)
    trailing_excluded_end = _floor_hour(session_end)
    landed_end = _floor_hour(now - timedelta(hours=archive_lag_hours))
    window_end = min(trailing_excluded_end, landed_end)
    if window_end <= window_start:
        return None
    return window_start, window_end


def write_session_gaps_seed(gaps: list[dict], session_id: str, seed_path: Path) -> None:
    """Overwrite `dbt/seeds/session_gaps.csv` with this session's gap intervals --
    `make recon`'s pre-step. Not committed back; the file is re-read fresh by every
    `make recon` run (the checked-in content is the AC1 fixture scenarios)."""
    with seed_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["session_id", "gap_start", "gap_end"])
        for gap in gaps:
            writer.writerow([session_id, gap["start"], gap["end"]])
