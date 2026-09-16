# Retro: Phase 2: Streaming

**Milestone:** Phase 2, Fargate ingester → Kinesis → Firehose → the same bronze; scripted session
lifecycle with manifest; bounded blast radius.
**Issues:** QNT-455, QNT-456, QNT-457, QNT-458, QNT-459 (all planned, all Done) + one reactive fix
(`tf-drift-check.yml` backend.hcl, PR #24) folded directly against QNT-474, no new ticket.
**Timeline:** 2026-09-07 16:22 UTC (QNT-455 / PR #22 merged) → 2026-09-08 16:40 UTC (QNT-459 / PR
#27 merged): ~24h18m elapsed, entirely inside Linear cycle 22 (2026-09-06 → 2026-09-13).

## What shipped

- **QNT-455**: Kinesis (on-demand) + Firehose Parquet-landing spike: JQ dynamic partitioning
  (`coin` `:`→`_`, `dt` from event `time`), `errors/` prefix, delivery-failure alarm. Applied and
  verified against real AWS, then destroyed. ADR-004 go/no-go: **PASS**. (PR #22)
- **QNT-456**: Python WebSocket ingester (`hyperlake.ingester`): trades subscription, shared
  bronze envelope, batched Kinesis `PutRecords` (retry only failed records), reconnect with
  exponential backoff + gap-interval recording, self-exit at `--max-session-hours`. (PR #23)
- **QNT-457**: Fargate ingester service + Firehose in the ephemeral layer: default VPC, public IP,
  egress-only SG (no VPC resources of our own), image tagged by commit SHA via a new
  `ingester-image.yml` OIDC workflow. G2(b) measured: 79s emission→bronze. (PR #25)
- **QNT-458**: `make session-up`/`session-down`: overlap guard (refuses to start over an
  unfinished manifest or a live Kinesis stream in state), applies/destroys the ephemeral stack,
  drains Firehose (≥120s), computes `cost_estimate_usd`, triggers `dbt-run` + `iceberg-maintain`,
  commits `sessions/<id>.json`. Loud non-zero exit on a dbt failure, stack still torn down first.
  (PR #26)
- **QNT-459**: Session reaper: a per-session one-time EventBridge Scheduler `at()` entry fires the
  dead-man's switch at `max_session_hours` (default 6) if `session-down` never runs, scales the
  ingester to 0, drains Firehose, deletes the Kinesis stream, writes a reap marker to S3.
  `session-down` reads that marker and marks the `costs/` row `reaper-terminated` instead of
  `pending` rather than silently looking like a normal teardown. (PR #27)

5 issues, 5 PRs, all merged as single-commit squashes, no multi-commit PR iteration, no rework
visible in the merged history. 0 rollovers, 0 descopes, 1 reactive fix folded into an existing
ticket (QNT-474) per CLAUDE.md's fold-follow-ups rule, not filed as new.

## What went well

- **Velocity.** 5 tickets in ~24h, the fastest phase yet, because Phase 1 had already proven the
  Terraform/session/OIDC patterns Phase 2 reused directly (persistent-vs-ephemeral split, the
  envelope schema, the `dbt-run.yml`/`scripts/gh_run.sh` dispatch contract).
- **Dev-execution ACs + adversarial review caught real bugs before merge, not after.** QNT-456's
  live AC4 run caught a self-exit path bug (`return` instead of `break`, skipping the final drain
  log); the follow-up adversarial review (flow-code-reviewer) found two more real gaps, a narrow
  reconnect exception catch missing `WebSocketException` subtypes, and `_flush()` silently
  discarding permanently-failed Kinesis records (an at-least-once violation), all three fixed and
  re-verified live inside the same PR. QNT-457's own verification caught the ingester never logging
  a subscription-confirmation event, making one of its own ACs unprovable; fixed with a pinning
  test in the same PR. This is the guard mechanism (dev-execution ACs + a second reviewer) doing
  exactly its job, not a gap.
- **A structurally-necessary compromise resolved itself as designed.** QNT-457's new
  `ingester-image.yml` workflow couldn't be dispatched pre-merge (GitHub refuses `workflow_dispatch`
  on a workflow not yet on the default branch), AC verification used dev credentials with an
  explicit "will confirm live post-merge" note in the PR. It did: the first real OIDC-authenticated
  run succeeded within minutes of merge (run `34240741360` and after).
- **The fold-not-file convention held.** The `tf-drift-check.yml` fix (see below) was shipped as a
  direct PR referencing the existing QNT-474 ticket, per explicit user direction, instead of
  spawning a new ops ticket for a one-line CI fix.

## Harder than expected / surprises

- **`tf-drift-check.yml` (QNT-474, shipped in Phase 1) was silently broken since its own merge.**
  `infra/main/backend.hcl` is gitignored (the bucket name embeds the AWS account ID), so a fresh
  checkout, exactly what the nightly scheduled workflow runs against, never had it, and every
  scheduled run failed at `terraform init` before the drift check itself ever executed. Caught
  mid-Phase-2 (PR #24, 2026-09-08) and fixed by writing `backend.hcl` from two new repo variables.
  QNT-474's own PR had proven `make tf-drift-check` locally against real AWS (dev credentials) and
  three times live via injected drift, but never actually dispatched the `tf-drift-check.yml`
  workflow file itself, so the workflow-level wiring gap escaped every AC.

## Invariant → guard audit

1. **Invariant:** "the nightly `tf-drift-check.yml` schedule actually completes a drift check.",
   **Violated** from QNT-474's merge (2026-09-07) through the PR #24 fix (2026-09-08), roughly a
   day of scheduled runs that failed silently at `terraform init` before drift logic ever ran.
   **Guard:** fixed in the workflow itself (writes `backend.hcl` from repo variables on checkout,
   `.github/workflows/tf-drift-check.yml`), proven by a real `workflow_dispatch` run. **Disposition:**
   fixed, and the class of gap it belongs to is closed by finding 2 below.
2. **Invariant (generalization):** "a new or modified GitHub Actions workflow is proven by actually
   triggering it, not by running its underlying script/logic locally.", **Violated** by QNT-474
   itself: its ACs proved `tf_drift_check.py`'s logic against real AWS, never the `tf-drift-check.yml`
   workflow's own runtime environment (checkout state, repo variables, OIDC). This is the same
   shape as QNT-473 (Phase 1): a mechanism verified via a stand-in, dev credentials there,
   local-script execution here, instead of its real execution context, and the real context turned
   out broken both times. **Guard:** `docs/AC-templates.md`'s "CI / dbt-run workflow changes"
   section was scoped narrowly to `ci.yml`/`dbt-run.yml`'s own contracts and didn't cover this
   class generically, broadened in this retro to require a real triggered run (`gh workflow run`
   or the workflow's natural trigger) with an observed success for *any* new/modified
   scheduled-or-dispatched workflow, before the ticket is called done. **Disposition:** fixed
   directly (doc is read and applied automatically by `flow-sanity-check`/`flow-review`, per its own
   header), no new ticket needed.

**Same-shape clustering:** this is the second consecutive phase where the root cause is "nothing
exercised the real execution environment (OIDC role, or now workflow runtime) until something
external forced it", Phase 1's QNT-473 (IAM scope) and Phase 2's `tf-drift-check.yml` gap (workflow
wiring) are two instances of one pattern, not two unrelated bugs. QNT-462 (Phase 3, Athena seam
test on every push) closes the IAM half by making OIDC exercise continuous instead of
manual-dispatch-only; the AC-template broadening above closes the workflow-wiring half the same
way finding 2 in Phase 1 was closed, an executable check applied by the pipeline, not a reminder.

## Lessons captured to memory

- New feedback memory `hyperlake-verify-workflow-dispatch`, a new/changed GitHub Actions workflow
  file (not just its underlying script) must be proven by an actual triggered run before the ticket
  is done; local execution of the same logic does not prove the workflow's own runtime environment
  (checkout state, secrets/vars, backend config). Generalizes the existing
  `hyperlake-verify-oidc-credential-path` lesson beyond OIDC/IAM specifically.
- `linear-hyperlake-project` updated with the Phase 2 issue range, cycle 22, and current status.

## Phase 3 review

Phase 3 (Convergence): QNT-460 (recon_trades/G3), QNT-461 (`make heal`), QNT-462 (Athena seam
test), QNT-463 (gold marts), QNT-464 (silver contract tests), QNT-465 (Reservoir fallback reader),
QNT-466 (live G3 replay e2e). All still in Backlog, none started. Cross-referenced against this
retro's findings:

- No invalidated requirements, no underspecified ACs, no complexity mismatches surfaced by Phase 2.
- **Dependency confirmed satisfied, not surfaced new:** Phase 1's retro flagged that QNT-458 would
  re-exercise the OIDC role live before QNT-462 lands, it did (QNT-458's AC1 proof is a live
  `dbt-run.yml` dispatch via `session-down`), so no action needed there.
- **Reinforced priority, not a scope change:** QNT-462 (Athena seam test) is now doing double duty,
  it was already the intended guard for Phase 1's OIDC-coverage finding, and per the same-shape
  clustering above it's also the natural place to keep proving the OIDC path continuously rather
  than only at ticket-verification time. No AC change needed; QNT-462's existing scope already
  covers this, flagging the priority, not editing the ticket.

**No ticket scope changes recommended for Phase 3.**

## Architecture overview

`docs/architecture/system-overview.md` updated: header bumped to "Phase 2 complete, QNT-455..459";
added the streaming data-flow (Fargate ingester → Kinesis → Firehose → bronze) and the session
lifecycle diagram (`session-up`/`session-down`/reaper) to the Architecture block; added
`ingester.py`/`session.py`/`session_reaper.py` to the `src/hyperlake/` line and a new `sessions/`
entry; added `ingester-image.yml` and `tf-drift-check.yml` to the GitHub Actions block and the
components table; updated the bronze data-store note to include `source=ws`; updated External
surfaces (three OIDC-exercising workflows, not one) and Infrastructure (session-bounded compute
cost, the reaper as the enforcement mechanism, the `tf-drift-check.yml` incident).

## Plan sync

Mechanical gap sweep: tracker set (31 issues, QNT-444..475, none cancelled) == plan set (31 ids
across Phase 0–4 + Ops & Reliability in `docs/project-plan.md`). Every Phase 2 item already ticked
`[x]`. Nothing further to sync.

## Project status update

Posted to the `Hyperlake` Linear project, health `onTrack`.
