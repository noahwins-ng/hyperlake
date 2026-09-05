# Retro — Phase 0: Scaffold

**Milestone:** Phase 0 — cost guardrails and the toolchain exist before any data resource does.
**Issues:** QNT-444, QNT-445, QNT-446, QNT-447 (all Done, cycle 1 / #21).
**Timeline:** 2026-09-04 18:29 UTC (QNT-444 merged) → 2026-09-05 16:00 UTC (QNT-447 merged) — ~21.5h elapsed.

## What shipped

- **QNT-444** — Python + Terraform toolchain, CI skeleton, Makefile. uv/pyproject, ruff, pyright,
  pytest; `ci.yml` on every PR with zero AWS creds. (PR #2)
- **QNT-445** — Terraform bootstrap: state backend (S3 + DynamoDB lock), GitHub OIDC role, Budgets
  $10 alarm + $15 create-deny action, `project` cost allocation tag, `docs/guides/bootstrap.md`.
  (PR #6)
- **QNT-446** — two-target dbt project skeleton (duckdb / athena), one materialization macro
  (ADR-002), placeholder model + gold-safe fixture, compiling in CI. (PR #7)
- **QNT-447** — `costs/sessions.csv` log schema (cost_estimate/cost_actual/status) + `make
  cost-backfill` against Cost Explorer, filtered by the `project` tag. (PR #13)

4 issues, 4 PRs, 0 rollovers, 0 descopes, 0 splits — one clean cycle.

## What went well

- All four issues landed in a single cycle with no rework beyond same-PR fixup commits (each
  merged PR had at most one small follow-up commit before merge, not after).
- The profile's convention of leaving `verify.*` empty with a documented reason (rather than
  guessing) worked as intended: `verify.security` was honestly marked TO-DERIVE right after
  QNT-444, then closed same-day once QNT-446 landed and `make check` / `pip-audit` were wired in.
- `tf-check`'s empty-vs-populated branching was written correctly the first time — it no-ops
  cleanly today and will exercise `terraform validate` for real once QNT-450 adds `.tf` files,
  with no rework needed at that boundary.
- Dependabot's first wave (aws provider, dbt-duckdb, dbt-athena-community, GitHub Actions) landed
  and merged same-day with zero incidents — the passive-complement design (ADR-independent) held.

## Harder than expected / surprises

- **CI-parity gap, self-corrected same day.** Right after QNT-444 merged, `workflow-profile.yaml`
  pointed `verify.test` at `make test` (pytest-only) with no synchronous dependency-audit gate.
  Once QNT-446 landed, a follow-up chore commit (0f2cbf0) added a `make check` target chaining
  every `ci.yml` step in order plus a `pip-audit` step, and the profile was refreshed. The gap was
  visible (documented, not silent) the whole time it existed.
- **Credential-scanner false positive.** QNT-445's bootstrap guide named `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` literally while asserting their *absence*, which tripped the repo's
  credential scanner. Fixed in the same PR by describing the invariant generically ("no long-lived
  AWS access key") instead of naming the env vars.
- Two PRs (QNT-444, QNT-445) each carried one small pre-merge fixup commit (an invalid
  `python-version` input to `astral-sh/setup-uv`; the doc wording above) — normal PR iteration,
  not incidents.

## Invariant → guard audit

1. **Invariant:** "the local sanity gate (`make check` / `verify.test`) mirrors `ci.yml` step-for-
   step, including a dependency-vulnerability gate." — **Violated** briefly (QNT-444 merge →
   QNT-446 merge, ~12h) while `verify.security` was empty and `make test` was pytest-only.
   **Guard:** `Makefile` `check` target (lint→format→types→test→audit→dbt-build→tf-check) +
   `.github/workflows/ci.yml` `pip-audit` step, both added in 0f2cbf0. **Disposition:** accepted
   risk, already closed — no lingering gap, no new ticket. This is the intended failure mode of
   the profile's "empty-with-a-reason" convention working correctly, not an incident needing a
   deeper guard.
2. **Invariant:** "no long-lived AWS credential material — including its own env-var names — ever
   appears in a committed doc." — **Violated** once (QNT-445 bootstrap guide), same-PR fix.
   **Guard:** none proposed; this is a wording convention, not a checkable rule (the scanner
   itself already does the enforcing). **Disposition:** accepted risk — captured as a doc-writing
   habit in memory (`hyperlake-avoid-secret-shaped-strings-in-docs`) rather than a new gate.

No same-shape clustering beyond the two above; neither needs a deeper unifying guard.

## Lessons captured to memory

- `hyperlake-phase0-retro` — refresh `workflow-profile.yaml` `verify.*` after the *last* toolchain
  ticket in a scaffold-style phase merges, not only once mid-phase.
- `hyperlake-avoid-secret-shaped-strings-in-docs` — describe credential absence generically in
  docs; naming the literal env-var trips the scanner even in a negation.

## Phase 1 review

Phase 1 (Lakehouse — batch): QNT-448 (done, OQ-1/ADR-005 closed pre-Phase-0), QNT-449..454 queued.
Cross-referenced against this retro's findings — no invalidated requirements, no underspecified
ACs, no new dependencies, no complexity mismatches surfaced. The two lessons above are process
habits (profile-refresh timing, doc wording) that apply going forward but don't change any Phase 1
ticket's scope. **No scope changes recommended.**

## Architecture overview

`docs/architecture/system-overview.md` updated: header bumped to "Phase 0 complete, QNT-444..447",
added the `costs/` layer and its table row, and noted the `pip-audit` CI step / `make check`
step-mirroring in the GitHub Actions block.

## Plan sync

Mechanical gap sweep: tracker set (28 non-cancelled issues, QNT-444..471) == plan set (28 ids in
`docs/project-plan.md`). All four Phase 0 items already ticked `[x]`. Nothing to sync.

## Project status update

Posted to the `Hyperlake` Linear project, health `onTrack`.
