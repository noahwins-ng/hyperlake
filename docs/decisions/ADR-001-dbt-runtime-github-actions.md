# ADR-001: Run dbt from a GitHub Actions workflow, not from AWS

- **Status:** accepted
- **Date:** 2026-09-04
- **Ticket:** — (PRD v0.5 review; lands in Phase 0)

## Context

The PRD names EventBridge + Step Functions as orchestration but never says where
dbt-athena itself executes. dbt needs a Python runtime with AWS credentials that can reach
Athena and the Glue catalog. Every option adds either a new AWS service, idle cost, or a
container build — all of which the PRD's beginner-AWS / near-zero-idle posture pushes
against. CI already has an OIDC role (NFR-3) and already runs dbt against DuckDB (FR-9).

## Decision

dbt runs in a **GitHub Actions workflow `dbt-run`**, triggered by `workflow_dispatch`. It
assumes the existing OIDC role and runs `dbt build --target athena`. Local scripts
(`make session-down`, the backfill runner) trigger it with `gh workflow run`.

Step Functions is scoped to the **backfill Lambda fan-out only** (one invocation per
archive hour file — [ADR-005](ADR-005-backfill-source-and-trade-identity.md); originally
written as coin × day before the 2026-09-04 spike). No dbt container is hosted in Lambda or
Fargate.

Stretch, Phase 4 only if time remains: a Step Functions state fires the same workflow via
repository dispatch, so the orchestration diagram shows the full chain without hosting dbt
in AWS.

## Alternatives considered

- **dbt-athena in a Lambda container image** — cold starts of 10–30 s, 15-min ceiling is
  fine today but not for a full-history rebuild, and it adds an ECR image + IAM surface
  for a beginner-AWS project.
- **dbt as a Fargate one-off task via Step Functions** — the "cloud-native" answer and a
  good resume signal, but it is a second container to build and debug, and it is the
  thing most likely to be left running by mistake (cost risk R4).
- **Run dbt from the laptop only** — zero infra, but not reproducible by a stranger
  (NFR-4) and leaves no audit trail of what ran when.

## Consequences

- Zero new AWS services, zero idle cost, and every dbt run is logged in Actions with the
  commit SHA that produced it — a free lineage record.
- dbt runs depend on GitHub availability and on a workflow round-trip (~1–2 min of
  overhead), which is acceptable because nothing here is latency-sensitive.
- The OIDC role needs Athena, Glue, and S3 permissions for the data buckets, so the
  Phase 0 bootstrap must grant them up front.
- The Phase 4 orchestration diagram will show GitHub Actions as a box inside the AWS
  flow; this must be drawn honestly rather than implying Step Functions runs dbt.
