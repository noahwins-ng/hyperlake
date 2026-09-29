# ADR-002: Two-target dbt project; local merge parity is a non-goal

- **Status:** accepted
- **Date:** 2026-09-04
- **Ticket:**: (PRD v0.5 review; lands in Phase 0)

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

- **`duckdb`**: silver is a plain `table`; dedup is
  `row_number() over (partition by tid order by source_rank desc, ingested_at desc) = 1`
  *(amended 2026-09-06, QNT-453: ordering by `ingested_at` alone could let a late `ws`
  duplicate outrank an already-applied `backfill` row; `source_rank` first keeps duckdb and
  athena agreeing on which row wins, per ADR-005's backfill-outranks-ws rule)*.
- **`athena`**: silver is `incremental`, Iceberg, `merge` on `tid`, bounded by a
  config-driven `dt` lookback window (default 2 days) so the merge prunes partitions.

CI runs `dbt build --target duckdb` on committed sample Parquet fixtures and proves
**column logic, uniqueness, and OHLCV math**. **Merge behaviour is proven only on Athena**,
but by an automated test, not by hoping the session script exercises it. Local merge
parity is an explicit non-goal.

*Amended 2026-09-04, Athena seam test:* a `seam_test` schema is seeded by dbt with a few
dozen fixture rows; `dbt build --select tag:seam` runs the silver merge twice, covering
(a) a late feed duplicate, (b) backfill arriving after the feed row, (c) a feed row
arriving after backfill, and asserts `first_seen_source`, flag preservation (source
precedence: backfill wins), and row counts. It runs inside the `dbt-run` workflow on
**every push to `main`**, using the OIDC role, not on pull requests. Cost per run is
cents (a few KB scanned).

*Amended 2026-09-24, QNT-481, seam test on pull requests:* the seam test also runs on every
same-repo pull request that touches `dbt/**`, in a separate `seam-pr` workflow, so a merge
regression fails before it lands instead of after. Each PR gets its own `seam_test_pr<N>`
schema, dropped after the run, and a per-PR `concurrency:` group, so parallel PRs cannot
race on one table. The OIDC role trusts the repo's `pull_request` subject; fork PRs
receive no id-token and skip. The offline `ci.yml` `checks` job is unchanged: CI still
runs with zero cloud credentials, and the cloud step lives in its own workflow. A Trino
container in CI was rejected: dbt-athena generates the `MERGE` itself (`update_condition`,
`merge_exclude_columns` are its own options), so a dbt-trino target would prove a
different materialization than the one that runs in production.

## Alternatives considered

- **Single target, Athena only**: no local development; every model edit costs an AWS
  round-trip and CI needs cloud credentials on every PR. Violates FR-9 and slows the
  weekend pace.
- **Make DuckDB write Iceberg too**: DuckDB's Iceberg extension is read-mostly and
  would introduce a second Iceberg writer, which the PRD forbids (only dbt-athena writes
  Iceberg).
- **Dispatch macros for every dialect difference**: works for functions, not for the
  merge itself; still leaves the seam untested locally while adding macro sprawl.

## Consequences

- One macro is the only place where the two targets differ; every model reads the same.
  Reviewers can see the seam in one file.
  *(Amended 2026-09-30, QNT-485: a second dialect helper, `json_string`, lives in the same
  file, `macros/materialization_for_target.sql`; no JSON function returns an unquoted string on
  both engines. The seam is still one file.)*
- A merge-specific bug is caught on push to `main` by the Athena seam test, not on the
  PR. A PR that breaks the merge therefore fails *after* merge; the workflow must be
  loud (required status on `main`, notification on failure) so that is acceptable for a
  single-owner repo. *(Superseded 2026-09-24, QNT-481: a PR touching `dbt/**` now runs the
  seam test before merge; the push-to-`main` run stays as the post-merge check.)*
- PR seam runs spend a few cents of Athena per dbt-touching PR and need the OIDC trust
  policy, per-PR schema IAM, and Terraform declaration kept in step (QNT-473, QNT-477).
- Sample fixtures must be small derived data (gold-safe), per the raw-data
  redistribution risk in the PRD.
- Dialect drift shows up as CI failures on DuckDB before it reaches Athena, which is the
  cheap direction to discover it.
