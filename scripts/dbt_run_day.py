"""`make dbt-run-day DAY=<YYYY-MM-DD>`: dispatch dbt-run.yml for one backfilled day.

Computes the dbt vars the README's run steps used to spell out as escaped JSON: a silver
lookback covering `DAY` through today, and a freshness window from `DAY` 00:00 to the next
day's 01:00 (the archive lands about an hour after a clock hour closes). Hands them to
scripts/gh_run.sh as one `-f vars=<json>` argument, so no shell quoting is involved.
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta


def day_vars(day: date, today: date) -> dict:
    return {
        "silver_lookback_days": (today - day).days + 1,
        "freshness_window_start": f"{day.isoformat()} 00:00:00",
        "freshness_window_end": f"{(day + timedelta(days=1)).isoformat()} 01:00:00",
    }


def gh_run_args(run_key: str, day: date, today: date) -> list[str]:
    return ["./scripts/gh_run.sh", run_key, "-f", f"vars={json.dumps(day_vars(day, today))}"]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: make dbt-run-day DAY=YYYY-MM-DD")
    day = date.fromisoformat(sys.argv[1])
    today = datetime.now(UTC).date()
    run_key = f"dbt-run-{int(time.time())}-{os.getpid()}"
    sys.exit(subprocess.run(gh_run_args(run_key, day, today)).returncode)


if __name__ == "__main__":
    main()
