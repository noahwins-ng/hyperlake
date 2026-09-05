# Hyperlake — System Overview

How the system actually works *now*. Kept current by `change-scope` (on scope changes) and `retro`
(against what actually shipped). If this drifts from reality it is worse than nothing.

> **As of 2026-09-06 (Phase 0 complete, QNT-444..447 shipped):** only the toolchain, the Terraform
> bootstrap, a dbt skeleton, and the cost log exist. No data path has been built. The target design
> is in [`docs/prd.md`](../prd.md) §5; this file describes only what is deployed or runnable today.

## Architecture

```
developer laptop ──► infra/bootstrap  (terraform, LOCAL state)
                        ├─ S3  hyperlake-tfstate-<account>   versioned, SSE, public-access blocked
                        ├─ DynamoDB hyperlake-tfstate-lock   pay-per-request
                        ├─ IAM OIDC provider + role hyperlake-github-actions
                        │     trust: repo:<github_repo>:*  ·  perms: athena/glue/s3/logs, prefix-scoped hyperlake*
                        ├─ Budgets hyperlake-monthly $10 alarm → email
                        │     + $15 action: attach deny-create (kinesis/ecs/lambda) to the GH role only
                        └─ CE cost allocation tag `project` (activation lags ≤ 24 h; re-apply)

                     infra/main  (terraform, S3 backend from bootstrap outputs, default_tags project=hyperlake)
                        ├─ persistent/   .gitkeep — data bucket, Glue, Athena workgroup land in QNT-450
                        └─ ephemeral/    .gitkeep — compute + streams land in Phase 1–2

GitHub Actions
  ci.yml          on PR + push main, ZERO AWS creds: ruff · pyright · pytest · pip-audit
                  · dbt build --target duckdb · terraform fmt/validate · grep for long-lived AWS keys
                  (`make check` mirrors this exact step order for local runs)
  verify-oidc.yml workflow_dispatch: assume the OIDC role, `sts get-caller-identity` — proves NFR-3

dbt/              two targets (ADR-002): duckdb (local/CI) · athena (env-driven, compiles offline)
                  one macro `materialization_for_target` owns the seam
                  one placeholder model stg_trades_sample over a committed gold-safe fixture

costs/            sessions.csv log (cost_estimate_usd / cost_actual_usd / cost_status) + `make
                  cost-backfill` (Cost Explorer, filtered by the `project` tag) — no sessions run yet
```

## Components / layers

| Layer | Responsibility | Status |
|-------|----------------|--------|
| `infra/bootstrap/` | one-time, local-state: state backend, OIDC role, budget alarm + deny action, cost tag | deployed (QNT-445) |
| `infra/main/` | S3-backend root with `persistent/` + `ephemeral/` modules | root only; modules empty |
| `dbt/` | two-target project, materialization macro, placeholder staging model | compiles + builds on duckdb in CI (QNT-446) |
| `src/hyperlake/` | shared package (envelope, watchlist, partition helper) | empty `__init__.py`; lands in QNT-449 |
| `scripts/spike/` | OQ-1 tid-parity gate (`capture_ws_trades.py`, `check_tid_parity.py`) | run once, gate passed 2026-09-05 (ADR-005) |
| `costs/` | session cost log schema + Cost Explorer backfill script | schema + `make cost-backfill` land (QNT-447); empty log, no sessions yet |
| CI (`ci.yml`) | offline gate, no cloud dependency, incl. `pip-audit` | green |

## Data stores

None yet. The only S3 bucket is Terraform state. Bronze / silver / gold arrive with QNT-450 / 453 / 463.

## External surfaces

- GitHub Actions → AWS via OIDC (`hyperlake-github-actions`), role ARN in repo variable
  `AWS_OIDC_ROLE_ARN`. No workflow other than `verify-oidc.yml` uses it yet; `dbt-run.yml` is QNT-454.
- Hyperliquid WS API and the requester-pays archive buckets are reached only by the spike scripts,
  from a developer laptop.

## Infrastructure

- Region `ap-northeast-1`; every resource carries `project=hyperlake` (provider `default_tags`).
- Idle cost today: S3 state bucket (KB) + DynamoDB on-demand lock table ≈ $0. Nothing runs.
- Bootstrap runbook: [`docs/guides/bootstrap.md`](../guides/bootstrap.md).
