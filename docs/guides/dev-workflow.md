# Dev workflow — weekly cadence

A cheat sheet for how the flow skills chain together (the command reference itself is CLAUDE.md).

## Cadence
- **Start of session** → `flow-session-check` (restore context) or `flow-status` (quick git glance).
- **Start of week** → `flow-cycle-start` (active cycle, suggested next pick).
- **Work an issue** → `flow-ship-issue <ID>` (pick → implement → sanity → review → ship).
- **Scope changed** → `flow-change-scope`, then `flow-sync-plan` if the plan drifted.
- **End of week** → `flow-cycle-end` (shipped, rollover, status update).
- **Milestone done** → `flow-retro <phase>` (invariant→guard audit, lessons, next-phase prep).

## New project (inception)
`flow-init` (import PRD) → `flow-doctor` → `flow-plan-project` (phases + Linear) →
`flow-gen-claudemd` → `flow-cycle-start`.

## Ops
- `flow-server-audit` — periodic prod durability/security/drift snapshot → tracked tickets.
