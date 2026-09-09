# Hyperlake — System Overview

How the system actually works *now*. Kept current by `change-scope` (on scope changes) and `retro`
(against what actually shipped). If this drifts from reality it is worse than nothing.

> **As of 2026-09-08 (Phase 2 complete, QNT-455..459 shipped):** both paths are live. Batch —
> archive hour file → Lambda backfill → bronze Parquet → silver Iceberg via `dbt-run` over OIDC,
> queryable in Athena. Streaming — Fargate ingester → Kinesis → Firehose → the same bronze table,
> under a scripted `session-up`/`session-down` lifecycle with a committed manifest, cost estimate,
> and a one-time EventBridge Scheduler dead-man's switch (the reaper) bounding blast radius if a
> session is forgotten. Convergence (recon, gap-healing, gold marts) is Phase 3.
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
                        │                workgroup `hyperlake` with a scan cap (QNT-450); ECR repo
                        │                for the ingester image (survives session teardown, QNT-457)
                        └─ ephemeral/    per-session stack, torn down by session-down:
                                         backfill Lambda + Step Functions Map fan-out over an hour
                                         list (QNT-451/452); Fargate ingester service + Kinesis
                                         (on-demand) + Firehose (QNT-457); session reaper Lambda +
                                         one-time EventBridge Scheduler `at()` schedule (QNT-459)

BATCH
  hl-mainnet-node-data hourly/YYYYMMDD/H.lz4 ──► Step Functions Map (make backfill) ──► Lambda
      (per hour) stream LZ4 → filter watchlist (config/watchlist.yaml) → collapse fill pairs
                                                                                 │
STREAMING (session-scoped, make session-up ... make session-down)                │
  Hyperliquid WS `trades` ──► Fargate ingester (src/hyperlake/ingester.py, default VPC, public IP,   │
      (per watchlist market)  egress-only SG, image tagged by commit SHA) ──► Kinesis (on-demand,    │
      envelope + PutRecords, reconnect w/ gap recording, self-exit at max_session_hours)             │
                                                        └─► Firehose (60s/64MB, JQ dynamic            │
                                                            partitioning, native Parquet vs Glue      │
                                                            schema) ───────────────────────────┐      │
                                                                                                ▼      ▼
                                              BRONZE  trades_raw   plain Parquet, coin=/dt=/source=
                                              (Glue partition projection; deterministic keys, idempotent)
                                                                                 │
  GitHub Actions dbt-run.yml (OIDC, run_key contract, scripts/gh_run.sh) ────────┘
      bronze → SILVER trades (Iceberg incremental merge on tid, dt lookback,
                               backfill outranks ws, insert-only first_seen_source)
      make iceberg-maintain (expire snapshots / rewrite manifests)
                                                            Athena workgroup `hyperlake` (cloud) ·
                                                            DuckDB (local + CI, dbt --target duckdb)

SESSION LIFECYCLE (src/hyperlake/session.py, scripts/session_{up,down}.py)
  make session-up    refuse-if-overlapping guard (unfinished manifest / live Kinesis in state) →
                      terraform apply ephemeral → start ingester → arm the reaper's one-time
                      schedule (max_session_hours, default 6) → sessions/<id>.json stub
  make session-down   stop ingester → drain Firehose (>=120s) → collect gap events → terraform
                      destroy ephemeral (tolerates a stream the reaper already deleted) → cost
                      estimate → costs/sessions.csv row (pending, or reaper-terminated if the
                      dead-man's switch fired first) → dbt-run + iceberg-maintain → commit manifest
  reaper (dead-man's switch, src/hyperlake/session_reaper.py)  fires once at max_session_hours if
                      nobody ran session-down: scales ingester to 0, drains Firehose, deletes the
                      Kinesis stream, writes a reap marker to S3 — the only channel back to
                      session-down, which has no other way to learn a session was reaped

GitHub Actions
  ci.yml            on PR + push main, ZERO AWS creds: ruff · pyright · pytest · pip-audit
                     · dbt build --target duckdb · terraform fmt/validate · grep for long-lived AWS keys
                     (`make check` mirrors this exact step order for local runs)
  dbt-run.yml        workflow_dispatch (run_key, select, target), OIDC creds, 20-min timeout, uploads
                      run_results.json — invoked by session-down, and by hand (QNT-454)
  ingester-image.yml  push-to-main (OIDC), builds + pushes the ingester image to ECR tagged by
                      commit SHA (QNT-457)
  tf-drift-check.yml  daily cron + workflow_dispatch (OIDC), compares the persistent layer's Glue
                      catalog against Terraform state (QNT-474); writes infra/main/backend.hcl from
                      repo variables on checkout — silently failed at `terraform init` on every
                      scheduled run for ~1 day until this step was added (PR #24, Phase 2 retro)

dbt/              two targets (ADR-002): duckdb (local/CI) · athena (OIDC, ap-northeast-1)
                  one macro `materialization_for_target` owns the seam
                  models/silver/trades.sql — Iceberg incremental merge on tid; stg_trades_sample
                  disabled on athena (QNT-473 — never wired to a real Glue source)

src/hyperlake/    envelope (typed columns + raw_payload), watchlist loader, partition-name helper
                  (xyz:SP500 -> xyz_SP500), backfill/{hour_list,official,state_machine} (QNT-449/451/452),
                  ingester.py (WS subscribe, envelope, Kinesis PutRecords, reconnect/gap, self-exit,
                  QNT-456), session.py (manifest, cost estimate, overlap guard, QNT-458),
                  session_reaper.py (dead-man's-switch Lambda, QNT-459)

sessions/         one committed manifest JSON per demo session (coins, start/end, deployed_sha,
                  gaps, dbt_runs, cost_estimate_usd, reaped/reaped_at) — lands QNT-458

costs/            sessions.csv log (cost_estimate_usd / cost_actual_usd / cost_status incl.
                  reaper-terminated) + `make cost-backfill` (Cost Explorer, filtered by the
                  `project` tag) — real session rows from QNT-455/458/459 verification runs
```

## Components / layers

| Layer | Responsibility | Status |
|-------|----------------|--------|
| `infra/bootstrap/` | one-time, local-state: state backend, OIDC role, budget alarm + deny action, cost tag | deployed; Glue/S3 policy scoped to real db names (QNT-445, QNT-473) |
| `infra/main/persistent/` | data bucket, Glue databases bronze/silver/gold, Athena workgroup, ECR repo | deployed in ap-northeast-1 (QNT-450, QNT-457) |
| `infra/main/ephemeral/` | backfill Lambda + Step Functions; Fargate ingester + Kinesis + Firehose; session reaper Lambda + one-time schedule | applied/destroyed per session by `session-up`/`session-down` (QNT-451/452, QNT-457, QNT-459) |
| `dbt/` | two-target project, materialization macro, silver Iceberg merge model | duckdb builds in CI; athena runs via `dbt-run.yml` over OIDC (QNT-446/453/454), invoked by `session-down` |
| `src/hyperlake/` | shared package: envelope, watchlist, partition helper, backfill reader, WS ingester, session manifest/cost/reaper | in use by the Lambda, Step Functions, Fargate task, and session scripts (QNT-449, QNT-456, QNT-458/459) |
| `scripts/spike/` | OQ-1 tid-parity gate (`capture_ws_trades.py`, `check_tid_parity.py`) | run once, gate passed 2026-09-05 (ADR-005) |
| `scripts/gh_run.sh` | dispatches `dbt-run.yml` by `run_key`, polls to completion, loud on failure/timeout | in use (QNT-454), called by `session-down` |
| `scripts/session_{up,down}.py` | session lifecycle: overlap guard, apply/destroy, manifest, cost estimate, dbt trigger | in use (QNT-458) |
| `sessions/` | one committed manifest per demo session | real rows land per session (QNT-458) |
| `costs/` | session cost log schema + Cost Explorer backfill script | schema (QNT-447); real session rows land per session (QNT-458/459) |
| CI (`ci.yml`, `ingester-image.yml`, `tf-drift-check.yml`) | offline gate; SHA-tagged image build/push; nightly Glue-vs-state drift check | green; `tf-drift-check.yml` fixed mid-Phase-2 (backend.hcl on fresh checkout, PR #24) |

## Data stores

- **bronze** (Glue db `bronze`, table `trades_raw`) — plain Parquet, Hive partitions
  `coin=/dt=/source=`, partition projection (no crawler/MSCK). Written by the backfill Lambda
  (`source=backfill`) and, per session, by Firehose from the live WS ingester (`source=ws`);
  append-only, never pruned.
- **silver** (Glue db `silver`, table `trades`, Iceberg) — merge on `tid`, `source_rank` picks
  `backfill` over `ws` on conflict, `first_seen_source` insert-only. Written only by `dbt-run.yml`
  over the OIDC role.
- **gold** — Glue db exists (imported into state, QNT-473); no models yet (Phase 3).

## External surfaces

- GitHub Actions → AWS via OIDC (`hyperlake-github-actions`), role ARN in repo variable
  `AWS_OIDC_ROLE_ARN`. Three workflows exercise it for real: `dbt-run.yml` (manual dispatch, or
  called by `session-down`), `ingester-image.yml` (auto on push to main), and `tf-drift-check.yml`
  (nightly cron + manual dispatch). Nothing yet runs a dbt build automatically on every push
  (that's QNT-462, Phase 3's Athena seam test).
- The archive bucket (`hl-mainnet-node-data`, requester-pays, ap-northeast-1) is read by the
  backfill Lambda per invocation, one hour file at a time.
- Hyperliquid WS `trades` channel is reached by the live Fargate ingester for the duration of a
  session (QNT-456/457), and by the spike scripts from a developer laptop for one-off captures.

## Infrastructure

- Region `ap-northeast-1`; every resource carries `project=hyperlake` (provider `default_tags`).
  The `silver` Glue database's earlier hand-created/import-into-state history (QNT-473) is now
  covered going forward by `tf-drift-check.yml` (QNT-474, daily), which itself shipped silently
  broken at `terraform init` (missing `infra/main/backend.hcl` on a fresh checkout) for about a day
  before being caught and fixed (PR #24) — see the Phase 2 retro's invariant/guard finding.
- Idle cost: S3 state + data buckets + DynamoDB on-demand lock table + Athena workgroup (no scan
  cost when idle) + ECR repo (image-storage only) ≈ under $1/month. Compute — Fargate ingester,
  Kinesis, Firehose, the session reaper — exists only inside a `session-up`/`session-down` window,
  bounded to `max_session_hours` (default 6) even if `session-down` is never run, by the reaper's
  one-time EventBridge Scheduler dead-man's switch (QNT-459). Recovery from a reaped session is
  documented in [`docs/guides/ops-runbook.md`](../guides/ops-runbook.md).
- Bootstrap runbook: [`docs/guides/bootstrap.md`](../guides/bootstrap.md).
