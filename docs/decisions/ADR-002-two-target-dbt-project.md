# ADR-002: Two-target dbt project; local merge parity is a non-goal

- **Status:** accepted
- **Date:** 2026-09-04
- **Ticket:** — (PRD v0.5 review; lands in Phase 0)

## Context

FR-9 requires dbt models to be testable on DuckDB without AWS, and CI to run them on every
PR. But silver's exactly-once contract is implemented as an Iceberg **incremental merge**
that only exists on dbt-athena: `table_type = 'iceberg'`, `incremental_strategy = 'merge'`,
and Athena `MERGE` semantics have no DuckDB equivalent. Trino SQL and DuckDB SQL also
diverge on date functions and decimal casting. Promising full parity would make CI fight
the production materialization forever.

## Decision

The dbt project has **two explicit targets** with materialization switched by a small
macro on `target.type`:

- **`duckdb`** — silver is a plain `table`; dedup is
  `qualify row_number() over (partition by tid order by ingested_at) = 1`.
- **`athena`** — silver is `incremental`, Iceberg, `merge` on `tid`, bounded by a
  config-driven `dt` lookback window (default 2 days) so the merge prunes partitions.

CI runs `dbt build --target duckdb` on committed sample Parquet fixtures and proves
**column logic, uniqueness, and OHLCV math**. **Merge behaviour is proven only on Athena**
— but by an automated test, not by hoping the session script exercises it. Local merge
parity is an explicit non-goal.

*Amended 2026-09-04 — Athena seam test:* a `seam_test` schema is seeded by dbt with a few
dozen fixture rows; `dbt build --select tag:seam` runs the silver merge twice, covering
(a) a late feed duplicate, (b) backfill arriving after the feed row, (c) a feed row
arriving after backfill, and asserts `first_seen_source`, flag preservation (source
precedence: backfill wins), and row counts. It runs inside the `dbt-run` workflow on
**every push to `main`**, using the OIDC role — not on pull requests. Cost per run is
cents (a few KB scanned).

## Alternatives considered

- **Single target, Athena only** — no local development; every model edit costs an AWS
  round-trip and CI needs cloud credentials on every PR. Violates FR-9 and slows the
  weekend pace.
- **Make DuckDB write Iceberg too** — DuckDB's Iceberg extension is read-mostly and
  would introduce a second Iceberg writer, which the PRD forbids (only dbt-athena writes
  Iceberg).
- **Dispatch macros for every dialect difference** — works for functions, not for the
  merge itself; still leaves the seam untested locally while adding macro sprawl.

## Consequences

- One macro is the only place where the two targets differ; every model reads the same.
  Reviewers can see the seam in one file.
- A merge-specific bug is caught on push to `main` by the Athena seam test, not on the
  PR. A PR that breaks the merge therefore fails *after* merge; the workflow must be
  loud (required status on `main`, notification on failure) so that is acceptable for a
  single-owner repo.
- Sample fixtures must be small derived data (gold-safe), per the raw-data
  redistribution risk in the PRD.
- Dialect drift shows up as CI failures on DuckDB before it reaches Athena, which is the
  cheap direction to discover it.
