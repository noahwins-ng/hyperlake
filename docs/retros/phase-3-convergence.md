# Retro: Phase 3: Convergence + transforms

**Milestone:** Phase 3, G3 proven at bronze, gaps healed, seam tested on Athena, gold marts with
data-quality gates.
**Issues:** QNT-460, QNT-461, QNT-462, QNT-463, QNT-464, QNT-465, QNT-466 (all planned, all Done) +
2 reactive Ops tickets folded in-window: QNT-476 (bronze ad-hoc query cost guardrail), QNT-477
(seam_test Terraform drift fix).
**Timeline:** 2026-09-09 15:45 UTC (QNT-460 / PR #29 merged) → 2026-09-11 17:30 UTC (QNT-466 / PR
#41 merged): ~2 days elapsed, spanning the end of Linear cycle 22 and the start of cycle 23.

## What shipped

- **QNT-460**: `recon_trades` over bronze: `ws_only`/`backfill_only`/`both` per `tid` per source
  over the reconcilable window, manifest gaps seeded in. Proves G3 (ADR-003): `ws_only = 0`, every
  `backfill_only` inside a gap. (PR #29)
- **QNT-461**: `make heal`: gap → covering hour list (+H+1 boundary rule) → not-landed check →
  re-run backfill Map over just those hours → re-run `dbt-run` (lookback sized to the oldest
  unhealed gap) → re-run recon → flip `healed: true` → commit manifest. Fixed two real bugs found
  in review before merge (below). (PR #31)
- **QNT-462**: Athena seam test: `seam_test` schema fixtures, `tag:seam` dbt selection, proves the
  silver merge (late duplicate, backfill-after-feed, feed-after-backfill) on real Athena. Runs
  automatically on every push to `main` via `dbt-run.yml`'s new `seam` job, the private repo's
  stand-in for a branch-protection required check. (PR #30)
- **QNT-463**: Gold marts: `ohlcv_1m/1h/1d`, `volume_daily`, `liquidations_daily` (backfill-only by
  construction), Iceberg incremental on athena / table on duckdb, OHLCV invariant tests (`low ≤
  open, close ≤ high`; candle volume = sum of trade `sz`). (PR #34)
- **QNT-464**: Silver contract tests: schema (`contract: enforced` on athena), freshness (max
  event `time` in-window), volume (against recon counts); gold gated on silver tests passing;
  `make dbt-demo-fail` for the Phase 4 demo. (PR #35)
- **QNT-465**: Reservoir fallback reader: column-mapped, schema-pinned, loud on layout drift,
  used when the official hour file is late/missing after 24h. (PR #36)
- **QNT-466**: Live G3 replay e2e: stream ≥ 1h on a real session, induce a disconnect, wait for the
  archive, heal, prove `ws_only = 0` with every `backfill_only` inside a recorded gap. First fully
  populated manifest (gaps healed, recon passing, dbt runs, cost estimate), the phase's demoable
  state and Phase 4's raw material. (PR #41)
- **QNT-476** (reactive, Ops & Reliability), `make bronze-query DT_FROM=...`: bounded ad-hoc Athena
  query wrapper, refuses to run unfiltered. Triggered by a 2026-09-09 cost investigation. (PR #37)
- **QNT-477** (reactive, Ops & Reliability), declared + imported the `seam_test` Glue database
  Terraform never knew about; `tf-drift-check` caught the drift the same day QNT-462 merged. (PR
  #32, #33)

7 planned issues, all merged, all Done. 0 rollovers, 0 descopes. 2 reactive ops tickets folded into
the existing perpetual Ops & Reliability milestone, not filed against Phase 3, consistent with
CLAUDE.md's fold-follow-ups convention and the pattern set in the Phase 1/2 retros.

## What went well

- **Dev-execution ACs + adversarial review caught two real pre-merge bugs on QNT-461, again.** Its
  own verification and a follow-up review found: (1) `make heal` originally read Terraform output
  for infra `session-down` had already destroyed by the time a gap exists, fixed by making the
  ephemeral apply/destroy lazy, only after the no-op/not-landed checks pass; (2) the session_id
  shell-injection guard validated the manifest's own `session_id`, but a different, unvalidated
  variable actually reached the shell-interpolated Terraform command, fixed by making
  `start_backfill`'s interface take the validated value explicitly. Both fixed and re-verified live
  inside the same PR, continuing the pattern the Phase 2 retro named: the guard mechanism (real
  execution ACs + a second reviewer) doing its job, not a gap.
- **The reactive guard from Phase 1's retro fired for real.** `tf-drift-check` (QNT-474, built after
  QNT-473's Phase 1 incident) caught the `seam_test` Glue database drift within a day of QNT-462
  merging, the first time this guard has actually caught something in production rather than being
  proven only by injected drift. QNT-477 fixed it the same way QNT-473 was fixed (declare + import).
- **The fold-not-file convention held again.** Both QNT-476 and QNT-477 shipped as small, separate
  tickets in the existing Ops & Reliability milestone rather than new phase-scoped tickets or scope
  creep on QNT-460/462.

## Harder than expected / surprises

- **QNT-461's two review-caught bugs** (above) took a real verification cycle: a synthetic manifest
  fixture (`sessions/qnt-461-ac3-verify.json`) was added, torn down, re-added post-fix, and removed
  again over about 40 minutes (20:18–20:59 UTC, 2026-09-10) to re-prove AC3/AC4 after the review
  fixes, not wasted time, but a visible rework cycle worth naming.
- **The `seam_test` schema drift (QNT-477)** was a genuine surprise in shape: QNT-473 (Phase 1) was
  a *hand-created* Glue database; this one was *auto-created by dbt-athena* as a side effect of the
  new `seam` job, with nobody having declared it in Terraform because nobody expected dbt itself to
  create infrastructure. Same invariant, a trigger nobody had cause to anticipate the first time.
- **The bronze ad-hoc query cost (QNT-476)** was a bigger number than expected: ~$0.62 of the
  project's ~$0.78 total AWS spend at the time of the 2026-09-09 investigation, from unbounded
  `SELECT` statements against `bronze.trades_raw`'s partition-projected table (no persisted
  metadata → an S3 LIST per virtual partition, ~4,090 of them). None of that spend was visible in
  any `costs/sessions.csv` row, it happened outside the session lifecycle entirely.

## Invariant → guard audit

1. **Invariant:** "every Glue database that exists live is declared in Terraform.", **Violated a
   second time**: QNT-473 (Phase 1, hand-created `silver`) and now QNT-477 (Phase 3, dbt-auto-created
   `seam_test`). **Guard:** the detective half worked, `tf-drift-check.yml` (QNT-474) caught this
   drift within a day, exactly as designed. But there was no *preventive* guard: nothing required a
   new dbt-created schema to ship its Terraform declaration in the same PR. **Disposition:** fixed
   directly, `docs/AC-templates.md`'s dbt model group now requires the paired
   `infra/main/persistent/glue.tf` declaration + `terraform import` in the same PR as any change
   that causes dbt-athena to auto-create a schema. No new ticket (same precedent as the Phase 2
   retro's AC-template broadening for workflow-dispatch proof).
2. **Invariant:** "the session_id used in a shell-interpolated Terraform command is the validated
   value, not a same-named-but-different variable.", **Violated** in `make heal`'s first draft
   (caught in review, before merge). **Guard:** fixed structurally in `scripts/heal.py`/
   `src/hyperlake/heal.py`, `start_backfill`'s interface now takes the validated value explicitly,
   so there's no second variable left to accidentally reach the shell call. **Disposition:** fixed;
   accepted that this class of bug (a validated variable shadowed by an unvalidated one at the call
   site) is caught by adversarial review rather than a lint rule, no generic static check for it
   exists in this codebase's toolchain, and one narrow instance doesn't yet justify building one.
3. **Invariant:** "ad-hoc queries against bronze stay cost-bounded.", **Violated**: ~$0.62 of
   ~$0.78 total spend, invisible to `costs/sessions.csv`. **Guard:** `scripts/bronze_query.py` +
   `make bronze-query` (QNT-476) requires a bounded `dt` lower bound and refuses to run otherwise;
   documented in `docs/guides/ops-runbook.md`. **Disposition:** fixed. Follow-on: QNT-469 (Phase 4
   cost report) gets a new AC to reconcile the Cost Explorer all-time total against
   `sum(cost_actual)` in `costs/sessions.csv` and explain any gap, so a future non-session-scoped
   cost doesn't silently disappear from the demo's cost claim the way this one did.

**Same-shape clustering:** finding 1 above is the *third* consecutive phase surfacing "a resource
exists live that Terraform doesn't know about" (QNT-473 in Phase 1, the shape itself; the
Phase 2 retro's `tf-drift-check.yml` gap was the adjacent shape "unverified execution context"; now
QNT-477 in Phase 3 is the same resource-drift shape as QNT-473, with dbt as the new trigger instead
of a human). The detective guard (`tf-drift-check`) has now proven itself twice, once against
injected drift, once for real. The AC-template addition above is the first *preventive* guard for
this specific shape; if a fourth instance appears with yet another trigger, that would be the signal
to stop treating this as three isolated tickets and build a single structural fix (e.g. a
pre-merge `terraform plan` diff against every schema dbt's compiled SQL would create).

## Lessons captured to memory

- New feedback memory `hyperlake-dbt-schema-needs-terraform-declaration`, a dbt config/macro that
  causes dbt-athena to auto-create a new Glue schema must ship its Terraform declaration + import in
  the same PR; the nightly drift check is a backstop, not the primary guard. Generalizes
  `hyperlake-verify-oidc-credential-path` and `hyperlake-verify-workflow-dispatch` into a third
  instance of "the real execution context finds gaps stand-ins don't."
- `linear-hyperlake-project` updated with the Phase 3 issue range, timeline, and current status.

## Phase 4 review

Phase 4 (Presentation): QNT-467 (README, already In Progress on the current branch), QNT-468 (demo
runbook), QNT-469 (recorded demo + cost report), QNT-470 (dbt docs on Pages). Cross-referenced
against this retro's findings:

- **No invalidated requirements, no complexity mismatches.** Phase 3 shipped exactly as scoped.
- **Underspecified AC found and fixed (approved by user, applied via change-scope):** QNT-469's cost
  report only checked each session's `cost_actual` and the idle month, it never reconciled against
  the Cost Explorer *total*, so the ad-hoc-query spend this retro found could recur in a different
  form and still pass QNT-469's existing ACs. Added AC4 (reconcile all-time total against
  `sum(cost_actual)`, explain any gap); updated `docs/project-requirement.md` and
  `docs/project-plan.md` to match; posted the scope-change audit comment on QNT-469.
- **Reinforced, not changed:** QNT-467's AC4 ("system-overview.md names every deployed component, no
  placeholder text") is now directly actionable, this retro's Step 6 update already closes it for
  everything Phase 3 shipped. QNT-470's new Pages-publishing workflow falls under the existing
  AC-template rule (any new scheduled/dispatched workflow needs a real triggered run) from the
  Phase 2 retro, no ticket change needed, just execute it.

## Architecture overview

`docs/architecture/system-overview.md` updated: header bumped to "Phase 3 complete, QNT-460..466";
added recon (`recon_trades`, G3), gold marts, silver contract tests, `make heal`, the Reservoir
fallback reader, and the seam test / `seam` job to the Architecture diagram, the `dbt/` and
`src/hyperlake/` blocks, and a new `scripts/{heal,recon,bronze_query}.py` line; added `recon`,
`gold`, and `seam_test` rows to Data stores; updated External surfaces (the seam job now exercises
OIDC automatically on every push, not just via manual dispatch/session-down); updated Infrastructure
with the `seam_test` drift incident and the ad-hoc query cost incident.

## Plan sync

Mechanical gap sweep: tracker set (33 issues, QNT-444..477, none cancelled) == plan set (33 ids
across Phase 0–4 + Ops & Reliability in `docs/project-plan.md`). Every Phase 3 item and both
reactive Ops tickets already ticked `[x]`. Nothing to sync.

## Project status update

Posted to the `Hyperlake` Linear project, health `onTrack`.
