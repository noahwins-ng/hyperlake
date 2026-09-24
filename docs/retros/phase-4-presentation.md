# Retro: Phase 4 — Presentation

**Milestone:** Phase 4, a hiring manager can absorb the project in ten minutes and a stranger can
reproduce it. **Issues:** QNT-467, QNT-468, QNT-469, QNT-470, QNT-478 (all planned, all Done). This
was the last PRD phase — all five milestones (0-4) are now shipped; only the perpetual Ops &
Reliability milestone remains open.
**Timeline:** 2026-09-11 17:38 UTC (QNT-467 started) → 2026-09-16 14:48 UTC (QNT-478 / PR #45
merged, all 5 tickets Done) → 2026-09-17 01:54 UTC (PR #54, the last untracked polish PR, hours
before the repo flipped public).

## What shipped

- **QNT-467**: README v1 with architecture diagram, per-layer sample queries, and the bootstrap
  path; carries the timed G1 reproduction (bootstrap/persistent no-op re-apply → ephemeral apply →
  1-day backfill → dbt-run → Athena query in 8m53s, live 2026-09-11). Fills
  `architecture/system-overview.md`. (PR #42)
- **QNT-468**: Demo runbook with real timings, session-up → stream → heal → recon → query. Live
  2026-09-12: session `qnt-468-20260912134330`, session-up 69.5s, ~12 min stream. (PR #43)
- **QNT-469**: Cost report + reconciliation, `costs/sessions.csv` against Cost Explorer's all-time
  total. The recorded-demo-video AC was dropped mid-ticket (OQ-4 amended 2026-09-15): the runbook's
  real measured command+output beat a muted, unlisted recording, so `docs/demo-runbook.md` is the
  demo artifact instead. (PR #44)
- **QNT-470**: `dbt docs generate` wired into CI, lineage graph captured for the README. Two ACs
  dropped via scope change 2026-09-16: live GitHub Pages (GitHub Free can't serve Pages from a
  private repo, which this was at the time) became a static screenshot; a live Cost Explorer
  screenshot AC was dropped since `costs/sessions.csv`/`docs/costs.md` already carried the cost
  proof. (PR #46)
- **QNT-478**: README restructured as a shareable portfolio landing page — problem-first framing,
  proof-first layout, design decisions, stack/repo tables, run/verify/tear-down, "what I would do
  differently"; internal vocabulary (G*/OQ-*/QNT-*/phase) swept out of the reader-facing copy. (PR
  #45)

**Untracked follow-up (2026-09-16 22:44 → 2026-09-17 01:54, PRs #47-#54):** a same-day "portfolio
review" pass, not tied to any ticket — README trimmed to the 248-line ceiling (24/7 cost model and
receipt moved to `costs/README.md`), every em dash replaced repo-wide, "Why this exists" rewritten
problem-first, a negative cost-report figure (`$-0.92/month`) clamped, `silver_lookback_days`
documented on the stranger path, ticket-id references stripped from `src/`/`dbt/` comments, a real
AWS account id replaced with a placeholder in test fixtures, and the docs updated to say the repo
is now public. The repo flipped public 2026-09-17 with `main` branch protection (required `checks`
status, linear history, no force push).

## Went well

- All 5 planned tickets shipped clean — one PR each, no review rework, no reopened issues.
- Both scope changes mid-phase (QNT-469's video AC, QNT-470's Pages/screenshot ACs) were caught and
  formalized through `flow-change-scope` rather than silently dropped or shipped broken.
- The G1 timed reproduction (< 15 min target) was proven live, not estimated: 8m53s.

## Harder than expected

- **The ticketed scope undercounted the real "make it presentable" cost.** After all 5 tickets
  closed Done, 8 more PRs of real fixes and polish landed the same week, none forecast by the plan.
  Writing docs a stranger will judge cold takes iteration a normal feature ticket doesn't.
- **GitHub Pages couldn't serve from the still-private repo** — an external platform constraint
  discovered mid-QNT-470, not knowable at plan time; handled by scope change to a static screenshot.
- **A same-day cascade of last-mile hygiene fixes** landed hours before the repo went public: a
  negative-looking cost figure, a real AWS account id briefly in test fixtures, and stray ticket-id
  comments outside the directories an earlier sweep covered. See the invariant audit below.

## Blockers

- GitHub Free's Pages-from-private-repo limitation (external platform constraint, resolved by scope
  change, not by waiting it out).

## Invariant & guard audit

1. **A real AWS account id sat in tracked test fixtures for ~21 minutes.**
   Invariant: no real account identifiers appear in tracked files, even test fixtures. — guard:
   NONE existed; introduced in QNT-471's PR #51 (2026-09-17 01:26), caught and fixed in PR #53
   (01:47) by manual review, not CI. **Disposition:** guard drafted, folded into QNT-479 below.
2. **`QNT-\d+` ticket-id comments were left in `.github/workflows/*.yml`.**
   Invariant: no ticket-id references in code comments (CLAUDE.md's "don't reference the current
   task" rule). — guard: NONE; an earlier sweep (cc2e8c1) only touched `src/` and `dbt/`, missing
   workflows. Same shape as the QNT-469 video-drop sweep that missed 5 doc references (see
   `hyperlake-scope-change-full-sweep` in memory) — a sweep scoped to the files a commit/skill
   happened to name, not the whole repo. **Disposition:** guard drafted, folded into QNT-479.
   **Correction (QNT-479 implementation, 2026-09-24):** the guard that shipped covers only
   `src/hyperlake/` and `dbt/`, not `.github/workflows/*.yml` itself. Re-reading `cc2e8c1` showed
   the repo deliberately keeps ticket-id comments in `scripts/`, `tests/`, `infra/*.tf`, and
   `.github/workflows/*.yml` as operational history (dozens of files, consistent since Phase 0) —
   only the reader-facing library/model code (`src/hyperlake/`, `dbt/`) was ever meant to stay
   clean. A repo-wide guard would have flagged that entire deliberate convention. So the workflow
   file this finding names is, and remains, intentionally uncovered — see QNT-479's ticket
   description and `tests/test_portfolio_lint.py` for the corrected scope.
3. **The README cost headline rendered a raw negative figure (`$-0.92/month`) from Cost Explorer
   lag.** Invariant: computed report figures never render as user-facing nonsense. — guard: NONE;
   caught by manual review, fixed ad hoc (81f10ca clamps it with an explanatory note).
   **Disposition:** guard drafted, folded into QNT-479.

**Same-shape clustering:** findings 1-3 share one shape — a cosmetic/hygiene defect with no
functional test coverage, caught only by eyeballing the diff in the final "portfolio review" pass
right before the repo went public. One guard replaces three narrow ones: **QNT-479**
(`make portfolio-lint`, wired into `ci.yml`'s `checks` job) — greps tracked files outside
docs/plan/retros for stray `QNT-\d+` comments, greps for the real AWS account id literal, and
asserts the README's `COST_REPORT` block never renders a negative-looking dollar figure. Filed
under Ops & Reliability, not accepted as risk, since the repo is now public and a repeat of any of
these three would be visible to a stranger.

## Lessons captured to memory

- `hyperlake-phase4-retro` — the presentation-phase-undercounts-its-cost pattern, and the
  same-shape hygiene-defect cluster with QNT-479 as the guard.
- `hyperlake-scope-change-full-sweep` — extended with the second instance (ticket-id sweep scoped
  to two directories instead of the whole repo).
- `linear-hyperlake-project` — updated: all 5 phases shipped, QNT-444..479, Ops & Reliability is now
  the only open milestone.

## Phase review: what's next

There is no next planned phase — Phase 4 was the last of the five PRD milestones (0 Scaffold, 1
Lakehouse, 2 Streaming, 3 Convergence, 4 Presentation), and all are now Done. The only milestone
still open is the perpetual **Ops & Reliability** catch-all, whose current item is QNT-479 (the
guard drafted above). No scope-change recommendations beyond that — the two mid-phase changes
already handled (QNT-469's video-AC drop, QNT-470's Pages/screenshot-AC drop) were the only
requirement changes this window, and both are already formalized in the spec/plan/PRD.

## Architecture overview

Updated `docs/architecture/system-overview.md`'s status header from "Phase 3 complete" to "Phase 4
complete, all five PRD phases now done," and expanded the Presentation summary to name what
actually shipped (README landing page, demo runbook as the demo artifact, cost reconciliation, dbt
docs in CI with a static lineage screenshot) instead of the old "is Phase 4" forward-reference.

## Cleanup

`docs/project-plan.md` synced: all Phase 4 items were already ticked; added QNT-479 as a new
unchecked item under Ops & Reliability with a `**Triggered by:**` note pointing at this retro. No
other gaps found between the tracker and the plan.
