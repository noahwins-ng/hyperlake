# Costs Log

`sessions.csv` — one row per demo session, committed to the repo (PRD G4, FR-7, FR-8).

| Column              | Meaning                                                                 |
|---------------------|--------------------------------------------------------------------------|
| `session_id`         | Unique id for the session (from the session manifest, Phase 2).         |
| `start`              | Session start, ISO 8601 UTC (e.g. `2026-09-01T10:00:00Z`).              |
| `end`                | Session end, ISO 8601 UTC.                                              |
| `cost_estimate_usd`  | Resource-hours × list price, computed at `session-down` (Phase 2; out of scope for QNT-447). |
| `cost_actual_usd`    | Cost Explorer's `UnblendedCost` for the session window, tag-filtered on `project=hyperlake`. Blank until backfilled. |
| `cost_status`        | `pending` until `cost_actual_usd` is filled, then `final`.              |
| `ce_query_date`      | UTC date `cost_actual_usd` was queried. Blank until backfilled.         |

## Why `cost_actual_usd` starts `pending`

The `project` cost allocation tag activates on AWS's own schedule (up to 24 h after first use),
and Cost Explorer itself lags actual billing by up to a further day (PRD FR-7). A session's
`cost_actual_usd` can't be known at `session-down` time, so it's recorded `pending` and filled in
later.

## Backfilling

`make cost-backfill` (`scripts/cost_backfill.py`) queries Cost Explorer once a `pending` row's
`end` is more than 24 h old, and rewrites that row `final`. Already-`final` rows are never
re-queried, so re-running the target is safe. `--dry-run` prints what would change without
writing the file.
