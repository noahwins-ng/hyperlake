# Retro: Phase 5, Production readiness

**Milestone:** Phase 5, the pipeline measures and reports its own quality and is shown to recover
from loss, at this scale and with no new AWS services. Added after the PRD by scope change
2026-10-03 (PR #91). **Issues:** QNT-487, QNT-488, QNT-489, QNT-491 (Done); QNT-490 (Canceled by
scope change, PR #92). **Audit window:** 2026-09-17 (Phase 4 retro cut-off) to 2026-10-03, so the
Ops & Reliability incidents QNT-480..486 and the untracked PRs #61 and #82 are covered here too.
**Timeline:** 2026-10-03 14:22 UTC (tickets filed) → 14:51 (QNT-487 started) → 16:42 (QNT-491 /
PR #96 merged, phase complete).

## What shipped

- **QNT-487**: `dq_daily` (in `models/quality/`, materialized in `gold`) reports per event day and
  coin: rows per source, within-source duplicate rate, WebSocket gap minutes, median archive lag.
  The silver freshness target (latest event `time` within 5 minutes of the run's window end) is
  stated in the README and system overview and cited by `assert_silver_freshness`. Fixed on the
  way: Athena's `session_gaps` seed held only fixture rows, so `gap_minutes` read 0 on real session
  days. (PR #93)
- **QNT-488**: `dq_daily.corrected_trades` counts trades whose archive row differs from the feed row
  on `px`, `sz` or `side`; `tests/test_late_correction.py` proves a late archive correction moves
  silver and the `ohlcv_1m` close. Measured result: 0 corrections on every session day (51,886
  shared trades on 2026-09-29 alone). (PR #94)
- **QNT-489**: README reconciliation chart (`scripts/recon_chart.py` → `docs/img/recon-2026-09-11.svg`),
  a "Not in scope at this scale" list, and the exactly-once bullet re-sourced to live Athena counts
  (the old "40 duplicates resolved" claim had no source). (PR #95)
- **QNT-491**: measured rebuild of silver and gold from bronze via a new `full_refresh` input on
  `dbt-run.yml`: 2 min 12 s, about $0.03, every count identical to pre-drill (silver 29,200,254).
  Ops runbook entry added. (PR #96)
- **Dropped, QNT-490** (open a GitHub issue on workflow failure): GitHub already emails the owner on
  failed scheduled and dispatched runs, `gh_run.sh` prints failures synchronously, and nothing runs
  24/7. Alerting moved to the README's not-in-scope list. (PR #92)

**Ops & Reliability in the same window:** QNT-480 (per-coin gap tracking, gap injection), QNT-481
(seam test on dbt PRs), QNT-482 (Iceberg data under the expiring `athena-results/` prefix), QNT-484
(`drop_seam_schema` guard), QNT-485 (HIP-3 coin name in silver/gold), QNT-486 (urllib3 CVE), plus
untracked #61 (cost attribution double-count) and #82 (seam-pr on dependency changes).

## Went well

- The phase shipped in under two hours: four tickets, one PR each, no review rework or reopened
  issues.
- Every execution AC carries a live Athena or workflow receipt, including the recovery drill's
  before/after counts on all five gold marts.
- The scope was cut early and on the record: QNT-490 was dropped through a scope change before any
  work started, not abandoned halfway.
- The measurement work produced a real finding (0 archive corrections) rather than only plumbing.

## Harder than expected

- **Every Athena-only difference from DuckDB had to be found live.** QNT-487 found Athena's
  `session_gaps` seed held only fixture rows; QNT-488 found dbt silently drops a new column on an
  existing Iceberg incremental model unless `on_schema_change` is set. Both passed `make check`
  first.
- **QNT-482 lost every silver and gold table.** dbt-athena wrote table data under `athena-results/`,
  which a 7-day lifecycle rule expires; it took two PRs (#66, then #67 for Hive seeds that ignore
  `s3_data_dir` under an enforced workgroup) and a full rebuild.
- **QNT-489 found README numbers that had drifted or never had a source** ("85 nodes", "40
  duplicates resolved").

## Blockers

None. Every Phase 5 dependency (Athena, OIDC dispatch, committed session manifests) was already in
place.

## Invariant & guard audit

1. **QNT-482**: Athena table data never lives under a lifecycle-expired prefix. Guard:
   `tests/test_athena_data_dir.py`, `scripts/check_athena_data_dir.sh` (run by `dbt-run.yml`).
2. **QNT-485 / QNT-480**: silver and gold `coin` is the exact HIP-3 name, and joins to bronze
   normalise through `coin_partition_value`. Guard: `dbt/tests/assert_hip3_coin_exact_name.sql`,
   bronze-shaped DuckDB fixtures, `tests/test_regen_recon_seed.py::test_hip3_gap_coin_is_written_as_its_partition_value`.
3. **QNT-487**: the Athena `session_gaps` seed carries every committed manifest's real gaps. Guard:
   `tests/test_regen_recon_seed.py::test_non_recon_build_appends_every_sessions_gaps_to_the_fixture_seed`.
4. **PR #82**: a dependency bump that can change the generated MERGE runs the seam test before
   merge. Guard: `seam-pr.yml` path filter includes `pyproject.toml` and `uv.lock`.
5. **QNT-484**: `drop_seam_schema` drops only `seam_test_pr<digits>`. Guard:
   `tests/test_drop_seam_schema_guard.py`.
6. **QNT-486**: the dependency set has no known vulnerabilities. Guard: `make audit` in the
   required `checks` job (it fired as designed).
7. **PR #61**: per-session costs never sum above the Cost Explorer total. Guard: `cost_report.py`
   fails on a negative reconciliation gap beyond $0.05. This also corrects the Phase 4 retro: the
   negative figure was double attribution of same-day sessions, not billing lag, and the clamp had
   hidden it.
8. **QNT-488**: a new column on an existing Iceberg incremental model is never silently dropped.
   Guard: explicit `on_schema_change` on both incremental models (`fail` on silver,
   `append_new_columns` on `dq_daily`); nothing forces a future incremental model to set it.
   **Accepted risk**: two incremental models exist and both set it.
9. **QNT-491**: silver and gold are rebuildable from bronze. Guard: the `full_refresh` input on
   `dbt-run.yml` and the measured runbook entry. **Accepted risk**: the drill is not re-run on a
   schedule; a rebuild is a few minutes and about $0.03 when needed.
10. **QNT-490 / QNT-482**: a ticket's Linear state changes only through the pipeline. Guard: NONE.
    QNT-490 went Canceled → Done when meta PR #92 (whose title named it) merged; QNT-482
    auto-closed on #66 with prod ACs pending. **Accepted risk** (owner decision 2026-10-04): no
    guard ticket; QNT-490 was reset to Canceled by hand during this retro.
11. **QNT-489**: README figures trace to a current source. Guard: NONE for hand-typed numbers (the
    generated cost block is pinned by `tests/test_cost_report.py`). **Disposition**: the hard-coded
    dbt node count was removed from the README in this retro, which removes the drifting figure;
    the remaining hand-typed figures carry query receipts in their PRs.

**Same-shape clustering:**
- Findings 1-4 (plus QNT-488's finding) share one shape: a stand-in environment (DuckDB fixtures,
  dependency-blind CI triggers) hid real Athena behaviour. The guards now in place (seam-pr with
  broadened triggers, fixtures in bronze's shape, the data-dir check) are the deeper guard; no new
  ticket.
- Findings 7 and 11 share a shape with the Phase 4 negative-cost finding: a reader-facing number
  not tied to the thing that computes it. Generated figures are guarded; hand-typed ones are kept
  few and receipted.

## Lessons captured to memory

- `hyperlake-phase5-retro`: the three shapes above (stand-in hides Athena, Linear's GitHub
  integration rewrites ticket state, hand-typed README figures drift).
- `linear-hyperlake-project`: updated to QNT-444..491, phases 0-5 shipped.

## Phase review: what's next

No next planned phase; Ops & Reliability is the only open milestone and it has no open issues.
Actions from this retro:
- [modify] README: drop the hard-coded dbt node count. Done in this retro's commit.
- Tracker fix: QNT-490 reset from Done to Canceled.
- Not taken: an Ops ticket to stop Linear auto-transitions on merge (owner declined, accepted
  risk), and reviving QNT-490 (QNT-482's red `seam` job was noticed within about a day through
  GitHub's default email, which supports the drop rationale).

## Architecture overview

`docs/architecture/system-overview.md` status header moved to "Phase 5 complete" and names what
shipped (`dq_daily` with the freshness target, the measured rebuild drill); the `dbt-run.yml`
component line now lists the `full_refresh` input.

## Cleanup

`docs/project-plan.md` already in sync: all Phase 5 and Ops items ticked, QNT-490 removed by PR #92.
The mechanical gap sweep found only QNT-472 and QNT-483, which belong to sibling projects.
`docs/INDEX.md`'s Retrospectives section was empty (no earlier retro had appended to it); it now
lists all six.
