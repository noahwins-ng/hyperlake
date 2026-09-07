# Hyperlake — System Overview

How the system actually works *now*. Kept current by `change-scope` (on scope changes) and `retro`
(against what actually shipped). If this drifts from reality it is worse than nothing.

> **As of 2026-09-07 (Phase 1 complete, QNT-448..454 + QNT-473 shipped):** the batch path is live —
> archive hour file → Lambda backfill → bronze Parquet → silver Iceberg via `dbt-run` over OIDC,
> queryable in Athena. Streaming (Fargate/Kinesis/Firehose) and session lifecycle are still Phase 2.
> The target design is in [`docs/prd.md`](../prd.md) §5; this file describes only what is deployed
> or runnable today.

## Architecture

```
developer laptop ──► infra/bootstrap  (terraform, LOCAL state)
                        ├─ S3  hyperlake-tfstate-<account>   versioned, SSE, public-access blocked
                        ├─ DynamoDB hyperlake-tfstate-lock   pay-per-request
                        ├─ IAM OIDC provider + role hyperlake-github-actions
                        │     trust: repo:<github_repo>:*  ·  perms: athena/glue(bronze/silver/gold)/
                        │     s3(data bucket)/logs — full dbt-athena IAM set (QNT-473)
                        ├─ Budgets hyperlake-monthly $10 alarm → email
                        │     + $15 action: attach deny-create (kinesis/ecs/lambda) to the GH role only
                        └─ CE cost allocation tag `project` (activation lags ≤ 24 h; re-apply)

                     infra/main  (terraform, S3 backend from bootstrap outputs, default_tags project=hyperlake)
                        ├─ persistent/   data bucket, Glue databases bronze/silver/gold, Athena
                        │                workgroup `hyperlake` with a scan cap (QNT-450)
                        └─ ephemeral/    backfill Lambda + Step Functions Map fan-out over an hour
                                         list, EventBridge trigger (QNT-451/452); streaming lands Phase 2

BATCH
  hl-mainnet-node-data hourly/YYYYMMDD/H.lz4 ──► Step Functions Map (make backfill) ──► Lambda
      (per hour) stream LZ4 → filter watchlist (config/watchlist.yaml) → collapse fill pairs
                                                                                 │
                                              BRONZE  trades_raw   plain Parquet, coin=/dt=/source=
                                              (Glue partition projection; deterministic keys, idempotent)
                                                                                 │
  GitHub Actions dbt-run.yml (OIDC, run_key contract, scripts/gh_run.sh) ────────┘
      bronze → SILVER trades (Iceberg incremental merge on tid, dt lookback,
                               backfill outranks ws, insert-only first_seen_source)
      make iceberg-maintain (expire snapshots / rewrite manifests)
                                                            Athena workgroup `hyperlake` (cloud) ·
                                                            DuckDB (local + CI, dbt --target duckdb)

GitHub Actions
  ci.yml          on PR + push main, ZERO AWS creds: ruff · pyright · pytest · pip-audit
                  · dbt build --target duckdb · terraform fmt/validate · grep for long-lived AWS keys
                  (`make check` mirrors this exact step order for local runs)
  dbt-run.yml     workflow_dispatch (run_key, select, target), OIDC creds, 20-min timeout, uploads
                  run_results.json — the only workflow that exercises the OIDC role for real (QNT-454)

dbt/              two targets (ADR-002): duckdb (local/CI) · athena (OIDC, ap-northeast-1)
                  one macro `materialization_for_target` owns the seam
                  models/silver/trades.sql — Iceberg incremental merge on tid; stg_trades_sample
                  disabled on athena (QNT-473 — never wired to a real Glue source)

src/hyperlake/    envelope (typed columns + raw_payload), watchlist loader, partition-name helper
                  (xyz:SP500 -> xyz_SP500), backfill/{hour_list,official,state_machine} (QNT-449/451/452)

costs/            sessions.csv log (cost_estimate_usd / cost_actual_usd / cost_status) + `make
                  cost-backfill` (Cost Explorer, filtered by the `project` tag) — no sessions run yet
```

## Components / layers

| Layer | Responsibility | Status |
|-------|----------------|--------|
| `infra/bootstrap/` | one-time, local-state: state backend, OIDC role, budget alarm + deny action, cost tag | deployed; Glue/S3 policy scoped to real db names (QNT-445, QNT-473) |
| `infra/main/persistent/` | data bucket, Glue databases bronze/silver/gold, Athena workgroup | deployed in ap-northeast-1 (QNT-450) |
| `infra/main/ephemeral/` | backfill Lambda + Step Functions Map fan-out, EventBridge trigger | deployed for backfill runs (QNT-451/452); streaming compute lands Phase 2 |
| `dbt/` | two-target project, materialization macro, silver Iceberg merge model | duckdb builds in CI; athena runs via `dbt-run.yml` over OIDC (QNT-446/453/454) |
| `src/hyperlake/` | shared package: envelope, watchlist, partition helper, backfill reader | in use by the Lambda and Step Functions (QNT-449) |
| `scripts/spike/` | OQ-1 tid-parity gate (`capture_ws_trades.py`, `check_tid_parity.py`) | run once, gate passed 2026-09-05 (ADR-005) |
| `scripts/gh_run.sh` | dispatches `dbt-run.yml` by `run_key`, polls to completion, loud on failure/timeout | in use (QNT-454) |
| `costs/` | session cost log schema + Cost Explorer backfill script | schema + `make cost-backfill` land (QNT-447); empty log, no sessions yet |
| CI (`ci.yml`) | offline gate, no cloud dependency, incl. `pip-audit` | green |

## Data stores

- **bronze** (Glue db `bronze`, table `trades_raw`) — plain Parquet, Hive partitions
  `coin=/dt=/source=`, partition projection (no crawler/MSCK). Written only by the backfill Lambda
  today; append-only, never pruned.
- **silver** (Glue db `silver`, table `trades`, Iceberg) — merge on `tid`, `source_rank` picks
  `backfill` over `ws` on conflict, `first_seen_source` insert-only. Written only by `dbt-run.yml`
  over the OIDC role.
- **gold** — Glue db exists (imported into state, QNT-473); no models yet (Phase 3).

## External surfaces

- GitHub Actions → AWS via OIDC (`hyperlake-github-actions`), role ARN in repo variable
  `AWS_OIDC_ROLE_ARN`. `dbt-run.yml` (manual dispatch) is the only workflow that exercises it for
  real; nothing yet runs it automatically on push (that's QNT-462, Phase 3's Athena seam test).
- The archive bucket (`hl-mainnet-node-data`, requester-pays, ap-northeast-1) is read by the
  backfill Lambda per invocation, one hour file at a time.
- Hyperliquid WS API is reached only by the spike scripts, from a developer laptop; the live
  ingester lands in Phase 2.

## Infrastructure

- Region `ap-northeast-1`; every resource carries `project=hyperlake` (provider `default_tags`),
  except the `silver` Glue database, which existed hand-created before being imported into state —
  see QNT-473 and the Phase 1 retro's invariant/guard finding (QNT-474 proposes a drift check).
- Idle cost: S3 state + data buckets (KB-scale, empty bronze/silver tables today) + DynamoDB
  on-demand lock table + Athena workgroup (no scan cost when idle) ≈ under $1/month. No compute
  runs outside a backfill invocation or a `dbt-run.yml` dispatch.
- Bootstrap runbook: [`docs/guides/bootstrap.md`](../guides/bootstrap.md).
