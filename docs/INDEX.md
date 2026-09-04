# Docs index

## Architecture
- [System overview](architecture/system-overview.md) — how the system works now

## Planning
- [Requirements](project-requirement.md) — what & why, per phase
- [Plan](project-plan.md) — execution tracker

## Decisions (ADRs)
<!-- One line per ADR, newest last. change-scope/sync-plan/retro append here. -->
- [ADR template](decisions/TEMPLATE.md)
- [ADR-001](decisions/ADR-001-dbt-runtime-github-actions.md) — dbt runs from a GitHub Actions workflow, not from AWS
- [ADR-002](decisions/ADR-002-two-target-dbt-project.md) — two-target dbt project; local merge parity is a non-goal
- [ADR-003](decisions/ADR-003-g3-reconciliation-at-bronze.md) — G3 convergence proven at bronze, not silver
- [ADR-004](decisions/ADR-004-kinesis-firehose-over-direct-write.md) — Kinesis + Firehose landing over a direct Fargate → S3 write

## Spikes
- [2026-09-04 OQ-1 archive desk spike](spikes/2026-09-04-oq1-archive-desk-spike.md) — measured both archives + WS feed: region, sizes, lag, grain, volume, decimals

## Retrospectives
<!-- One per completed milestone; retro appends here. -->
