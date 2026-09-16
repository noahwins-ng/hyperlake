# Docs index

## Architecture
- [System overview](architecture/system-overview.md): how the system works now

## Planning
- [Requirements](project-requirement.md): what & why, per phase
- [Plan](project-plan.md): execution tracker

## Decisions (ADRs)
<!-- One line per ADR, newest last. change-scope/sync-plan/retro append here. -->
- [ADR template](decisions/TEMPLATE.md)
- [ADR-001](decisions/ADR-001-dbt-runtime-github-actions.md): dbt runs from a GitHub Actions workflow, not from AWS
- [ADR-002](decisions/ADR-002-two-target-dbt-project.md): two-target dbt project; local merge parity is a non-goal
- [ADR-003](decisions/ADR-003-g3-reconciliation-at-bronze.md): G3 convergence proven at bronze, not silver
- [ADR-004](decisions/ADR-004-kinesis-firehose-over-direct-write.md): Kinesis + Firehose landing over a direct Fargate → S3 write
- [ADR-005](decisions/ADR-005-backfill-source-and-trade-identity.md): official archive primary, ap-northeast-1, `tid` as trade identity (closes OQ-1; PRD frozen)

## Spikes
- [2026-09-04 OQ-1 archive desk spike](spikes/2026-09-04-oq1-archive-desk-spike.md): measured both archives + WS feed: region, sizes, lag, grain, volume, decimals
- [2026-09-11 QNT-466 G3 live replay](spikes/2026-09-11-qnt466-g3-live-replay.md): first full-hour live session: 4 bugs found + fixed (liquidation coercion, freshness vars, gh_run.sh --ref, gap boundary), one documented residual

## Retrospectives
<!-- One per completed milestone; retro appends here. -->
