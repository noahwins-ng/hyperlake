"""Gap -> hour-file expansion + dbt lookback sizing for `make heal` (FR-8).

Backfill hour files are cut by block *arrival* time (see `hyperlake.backfill.hour_list`'s
docstring) -- a WS gap ending near the top of hour `H` can have its events land in hour
`H+1`'s file instead, so healing a gap needs the same trailing-hour boundary rule
`hour_list.hour_list` applies to a `[FROM, TO]` backfill range, just walked at hour (not
day) granularity from the gap's own start/end.
"""

from datetime import date, datetime, timedelta


def gap_to_hours(gap_start: datetime, gap_end: datetime) -> list[dict[str, str]]:
    """Archive-native `{date, hour}` items (`date` as `YYYYMMDD`) covering every hour
    file that could hold an event whose `time` falls in `[gap_start, gap_end)`, plus one
    trailing hour past the last one touched (boundary rule)."""
    if gap_end <= gap_start:
        raise ValueError(f"gap_end ({gap_end}) must be after gap_start ({gap_start})")

    cur = gap_start.replace(minute=0, second=0, microsecond=0)
    hours = []
    while cur < gap_end:
        hours.append({"date": cur.strftime("%Y%m%d"), "hour": str(cur.hour)})
        cur += timedelta(hours=1)
    hours.append({"date": cur.strftime("%Y%m%d"), "hour": str(cur.hour)})
    return hours


def lookback_days(gaps: list[dict], today: date) -> int:
    """`silver_lookback_days` for `make heal`'s dbt-run, sized to re-cover the oldest
    unhealed gap's date: `today - gap_start_date + 1` (AC5) -- never the dbt default
    (2), which would silently skip an old gap's `dt` partition on merge. 0 if there is
    nothing unhealed (caller shouldn't be running dbt-run at all in that case)."""
    unhealed = [g for g in gaps if not g.get("healed", False)]
    if not unhealed:
        return 0
    oldest_start = min(datetime.fromisoformat(g["start"].replace("Z", "+00:00")) for g in unhealed)
    return (today - oldest_start.date()).days + 1
