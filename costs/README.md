# Costs Log

`sessions.csv`, one row per demo session, committed to the repo (PRD G4, FR-7, FR-8).

| Column              | Meaning                                                                 |
|---------------------|--------------------------------------------------------------------------|
| `session_id`         | Unique id for the session (from the session manifest, Phase 2).         |
| `start`              | Session start, ISO 8601 UTC (e.g. `2026-09-01T10:00:00Z`).              |
| `end`                | Session end, ISO 8601 UTC.                                              |
| `cost_estimate_usd`  | Resource-hours × list price, computed at `session-down` (Phase 2; out of scope for QNT-447). |
| `cost_actual_usd`    | Cost Explorer's `UnblendedCost` for the session window, tag-filtered on `project=hyperlake`. Blank until backfilled. |
| `cost_status`        | `pending` until `cost_actual_usd` is filled, then `final`. A session ended by the reaper (`docs/guides/ops-runbook.md`) starts `reaper-terminated` instead of `pending`, and keeps that label even after backfill -- distinct from a normal teardown, not a fourth "unfilled" state. |
| `ce_query_date`      | UTC date `cost_actual_usd` was queried. Blank until backfilled.         |

## Why `cost_actual_usd` starts `pending`

The `project` cost allocation tag activates on AWS's own schedule (up to 24 h after first use),
and Cost Explorer itself lags actual billing by up to a further day (PRD FR-7). A session's
`cost_actual_usd` can't be known at `session-down` time, so it's recorded `pending` and filled in
later.

## Backfilling

`make cost-backfill` (`scripts/cost_backfill.py`) queries Cost Explorer once a `pending` or
`reaper-terminated` row's `end` is more than 24 h old, and fills `cost_actual_usd` --
`pending` rows are rewritten `final`; `reaper-terminated` rows keep their label (only the
dollar figure is filled in). Already-`final` rows are never re-queried, so re-running the
target is safe. `--dry-run` prints what would change without writing the file.

## Reporting

`make cost-report` (`scripts/cost_report.py`, QNT-469) renders this table into
[`docs/costs.md`](../docs/costs.md) and the README's Cost section, and asserts cost
discipline: every session older than 48 h must have `cost_actual_usd` filled, every
`cost_actual_usd` and every month's idle spend (Cost Explorer total minus that month's
session actuals) must be under the PRD G4 $2 ceiling, and Cost Explorer's all-time
`project=hyperlake` total is reconciled against `sum(cost_actual_usd)` here. Needs AWS
credentials; not part of `check`/CI.

## What 24/7 would cost

Hyperlake never runs continuously; this is the always-on option priced with the same
estimator `session-down` uses (`hyperlake.session.estimate_cost_usd`, ap-northeast-1 list
prices from PRD §8), at 720 h. A model, not a measurement.

| Line item | 30 days, 24×7 |
|---|---|
| Fargate | $11.52 |
| Kinesis stream-hours | $34.56 |
| Kinesis + Firehose per-GB (~9.9 GB raw/month) | $1.80 |
| Athena maintenance (`iceberg-maintain`) | $0.01 |
| Overhead margin | $54.00 |
| **Model total** | **$101.89** |
| Model total, excluding the overhead margin | $47.89 |

Measured sessions have consistently come in under the estimator (the overhead margin is
padded), so the realistic range is $50–100/month. Of the metered spend, Kinesis's on-demand
hourly charge is ~72% ($34.56 of $47.89) and accrues whether or not a trade arrives.

Receipt (2026-09-16, regenerated from the estimator, not hand-typed):

```
$ uv run python -c "
from hyperlake.session import estimate_cost_usd, FARGATE_HOURLY_USD, KINESIS_HOURLY_USD, KINESIS_PER_GB_USD, FIREHOSE_INGEST_PER_GB_USD, FIREHOSE_CONVERSION_PER_GB_USD, FIREHOSE_PARTITION_PER_GB_USD, FIREHOSE_PARTITION_PER_1K_OBJECTS_USD, OVERHEAD_HOURLY_USD, BYTES_PER_SECOND, FIREHOSE_AVG_OBJECT_BYTES
h = 720; raw_gb = BYTES_PER_SECOND * h * 3600 / 1e9; objs = raw_gb * 1e9 / FIREHOSE_AVG_OBJECT_BYTES
fargate = FARGATE_HOURLY_USD * h; kinesis_hourly = KINESIS_HOURLY_USD * h
per_gb = KINESIS_PER_GB_USD * raw_gb + (FIREHOSE_INGEST_PER_GB_USD + FIREHOSE_CONVERSION_PER_GB_USD) * raw_gb + FIREHOSE_PARTITION_PER_GB_USD * raw_gb + FIREHOSE_PARTITION_PER_1K_OBJECTS_USD * (objs / 1000)
overhead = OVERHEAD_HOURLY_USD * h; total = estimate_cost_usd(h)
print(f'fargate={fargate:.2f} kinesis_stream_hours={kinesis_hourly:.2f} kinesis_firehose_per_gb={per_gb:.2f} overhead_margin={overhead:.2f} total={total} total_excl_overhead={round(total-overhead,2)}')
"
fargate=11.52 kinesis_stream_hours=34.56 kinesis_firehose_per_gb=1.80 overhead_margin=54.00 total=101.89 total_excl_overhead=47.89
```
