"""Builds the archive hour-file list for a `make backfill FROM= TO=` run.

Hour files are cut by block *arrival* time, so a fill whose event `time` falls near the
top of hour `H` can land in the `H+1` file instead (spike 2026-09-04, "Hour-file
boundary"). To capture every event whose `time` falls within `[FROM, TO]`, the fan-out
must include one trailing hour past `TO`: hour `0` of the day after `TO`.
"""

from datetime import date, timedelta


def hour_list(from_date: str, to_date: str) -> list[dict[str, str]]:
    """Archive-native `{date, hour}` items (`date` as `YYYYMMDD`) for every hour in
    `[from_date, to_date]` (both ISO `YYYY-MM-DD`, inclusive), plus the trailing hour."""
    from_d = date.fromisoformat(from_date)
    to_d = date.fromisoformat(to_date)
    if to_d < from_d:
        raise ValueError(f"TO ({to_date}) is before FROM ({from_date})")

    hours = []
    d = from_d
    while d <= to_d:
        hours.extend({"date": d.strftime("%Y%m%d"), "hour": str(h)} for h in range(24))
        d += timedelta(days=1)
    hours.append({"date": d.strftime("%Y%m%d"), "hour": "0"})
    return hours
