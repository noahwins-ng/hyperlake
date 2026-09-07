# Retro — Phase 1: Lakehouse (batch)

**Milestone:** Phase 1 — archive → Lambda backfill → bronze Parquet → silver Iceberg via `dbt-run`
→ queryable in Athena.
**Issues:** QNT-448, QNT-449, QNT-450, QNT-451, QNT-452, QNT-453, QNT-454 (all planned, all Done)
+ QNT-473 (reactive fix, folded into the phase). Cycle 1 / #21 tail → cycle 2 / #22 start.
**Timeline:** 2026-09-04 17:50 UTC (QNT-448 / OQ-1 closed, ADR-005) → 2026-09-07 13:48 UTC
(QNT-473 merged) — ~68h elapsed; the dense build window was 2026-09-06 02:01 → 09-07 13:48
(~36h) since Phase 0's tail (QNT-445..447) was still finishing through 09-05.

## What shipped

- **QNT-448** — closed OQ-1: `tid` parity between a live WS capture and the official archive hour
  file, ADR-005. The last gate blocking the rest of the phase.
- **QNT-449** — shared `hyperlake` package: envelope schema (typed columns + `raw_payload`),
  `config/watchlist.yaml` loader, partition-name helper (`xyz:SP500` → `xyz_SP500`). (PR #14)
- **QNT-450** — persistent layer: data bucket, Glue databases `bronze`/`silver`/`gold`, bronze
  `trades_raw` with partition projection, Athena workgroup `hyperlake` — live in ap-northeast-1.
  (PR #15)
- **QNT-451** — backfill Lambda: streams LZ4 hour files, filters the watchlist, collapses fill
  pairs, writes bronze at deterministic idempotent keys (`coin=/dt=/source=backfill/hour=H.parquet`).
  (PR #16)
- **QNT-452** — Step Functions Map fan-out over an hour list + `make backfill`; 1-day and 30-day
  backfill timing recorded (G1). (PR #17)
- **QNT-453** — silver `trades` model: Iceberg incremental merge on `tid`, `dt` lookback,
  `source_rank` (backfill outranks ws), insert-only `first_seen_source`. (PR #18)
- **QNT-454** — `dbt-run.yml`: GitHub Actions workflow_dispatch over OIDC with a `run_key`
  completion contract (`scripts/gh_run.sh`), 20-min timeout, `make iceberg-maintain`. (PR #19)
- **QNT-473** — reactive fix, found while verifying QNT-454 AC4: the OIDC role's Glue IAM policy
  was scoped to an unused `hyperlake*` prefix and never actually covered `bronze`/`silver`/`gold`;
  fixed to the real database names and dbt-athena's full documented IAM action set. Also imported
  the live-but-hand-created `silver` Glue database into Terraform state. (PR #20)

8 issues, 7 PRs (QNT-448 closed via a docs commit, no PR — pre-Phase-1 spike gate), 0 rollovers,
0 descopes, 1 reactive addition folded into the same milestone.

## What went well

- Every planned ticket landed in sequence with no rework: 6 of 7 PRs were single-commit; QNT-453
  carried two small CI-fixup commits (dbt subprocess flags for the compile step) — normal PR
  iteration, not an incident.
- The batch path is now demonstrably live end-to-end, not just individually-tested per layer:
  QNT-473's fix was proven by three real `dbt-run.yml` dispatches through the OIDC role, the last
  one showing `silver.trades` idempotent at 25,677,879 rows.
- The OQ-1 gate (QNT-448) did its job — closing the bronze data model first meant zero schema
  churn across QNT-449 through QNT-453.

## Harder than expected / surprises

- **QNT-473 (unplanned).** The OIDC role's Glue policy gap and the hand-created `silver` database
  were both invisible for nearly the whole phase because every prior dbt-athena run and Glue
  resource creation (QNT-449/450/453) went through a developer's own broad AWS credentials, not
  the OIDC role. The gap surfaced only when `dbt-run.yml` (QNT-454) first exercised that role for
  real, then took three live dispatch-fix-dispatch cycles to fully resolve (`CreateDatabase` →
  `s3:DeleteObject` → `glue:GetTableVersions`).
- Two small CI-fixup commits on QNT-453 (dbt-athena compile subprocess flags) — normal iteration.

## Invariant → guard audit

1. **Invariant:** "the OIDC role's IAM policy actually covers dbt-athena's real workload (the
   live Glue databases, S3 paths, and actions it needs)." — **Violated** for nearly all of Phase 1
   (QNT-449 merge → QNT-473 fix, ~46h of real elapsed build time), invisible because verification
   never went through the OIDC role until QNT-454/473. **Guard:** none automated yet — QNT-473 was
   proven by three manual live `dbt-run.yml` dispatches inside that one PR, not by CI. The intended
   guard is QNT-462 (Athena seam test, `tag:seam`, on every push to `main`, Phase 3) — it will run
   a real OIDC-authenticated dbt build automatically. **Disposition:** accepted risk until QNT-462
   lands. Flagged for Phase 2 review below: QNT-457/458 will exercise the OIDC role again before
   QNT-462 ships.
2. **Invariant:** "every AWS resource is Terraform-managed and tagged `project=hyperlake`; nothing
   hand-created." — **Violated**: the `silver` Glue database existed live, hand-created by a prior
   dev-credentialed run, never `terraform apply`'d — undetected through most of Phase 1, caught
   only by review during QNT-473. **Guard:** none existed; QNT-471 (post-destroy audit) only
   covers lingering *billable ephemeral* resources after teardown, not drift on the *persistent*
   layer. **Disposition:** new ticket filed — **QNT-474** (Terraform state drift check on the
   persistent layer, Ops & Reliability), approved by the user during this retro.

**Same-shape clustering:** findings 1 and 2 share one root cause — nothing in Phase 1 exercised
the real OIDC/production-credential path until the phase's last ticket, so both the IAM-scope gap
and the hand-created database escaped notice the same way. QNT-462 (path-exercise guard) and
QNT-474 (drift guard) are two narrow fixes for the same underlying gap; no single deeper guard
replaces both, since one checks permissions-in-use and the other checks resources-in-state, but
they should be thought of as a pair, not independent hardening.

## Lessons captured to memory

- `hyperlake-verify-oidc-credential-path` — verify Hyperlake infra/IAM changes by actually
  exercising the OIDC role (e.g. dispatch `dbt-run.yml`), not by running `terraform apply`/`dbt
  build` locally under a developer's broad personal AWS credentials — the QNT-473 gap was invisible
  under local credentials for the whole phase.
- `linear-hyperlake-project` updated with the Phase 1 issue range and cycle boundaries.

## Phase 2 review

Phase 2 (Streaming): QNT-455 (Kinesis/Firehose spike), QNT-456 (WS ingester), QNT-457 (Fargate +
Firehose, G2), QNT-458 (session-up/down), QNT-459 (session reaper). Cross-referenced against this
retro's findings:

- No invalidated requirements, no underspecified ACs, no complexity mismatches.
- **Dependency surfaced:** QNT-457/458 will invoke the OIDC role again (session-up calls `dbt-run`
  + `iceberg-maintain`) before QNT-462 (the automated seam-test guard) lands in Phase 3. The
  streaming path itself adds no new Glue objects (Firehose writes into the existing bronze
  bucket/database), so no IAM-scope change is expected — but whoever ships QNT-458 should
  re-verify the OIDC dispatch succeeds live, per the lesson above, rather than assuming QNT-473's
  fix is permanently sufficient.

**No ticket scope changes recommended for Phase 2** beyond the note above (not a scope change,
just a verification reminder tied to existing ACs).

## Architecture overview

`docs/architecture/system-overview.md` updated: header bumped to "Phase 1 complete, QNT-448..454 +
QNT-473"; added the batch data-flow diagram (archive → Step Functions → Lambda → bronze → dbt-run
→ silver), a Data stores section (bronze/silver/gold with ownership and write path), the
`dbt-run.yml` / `scripts/gh_run.sh` surfaces, and noted the `silver` database's import-into-state
history under Infrastructure.

## Plan sync

Mechanical gap sweep: tracker set (30 non-cancelled issues, QNT-444..471 + QNT-473 + QNT-474) ==
plan set (30 ids in `docs/project-plan.md`, QNT-474 added by this retro's change-scope step).
Every Phase 1 item already ticked `[x]`. Nothing further to sync.

## Project status update

Posted to the `Hyperlake` Linear project, health `onTrack`.
