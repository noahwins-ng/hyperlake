# Hyperlake: System Overview

How the system actually works *now*. Kept current by `change-scope` (on scope changes) and `retro`
(against what actually shipped). If this drifts from reality it is worse than nothing.

> **As of 2026-09-11 (Phase 3 complete, QNT-460..466 shipped):** both paths are live and converge.
> Batch, archive hour file → Lambda backfill (official reader, Reservoir fallback on drift) →
> bronze Parquet → silver Iceberg via `dbt-run` over OIDC, queryable in Athena. Streaming, Fargate
> ingester → Kinesis → Firehose → the same bronze table, under a scripted
> `session-up`/`session-down` lifecycle with a committed manifest, cost estimate, and a one-time
> EventBridge Scheduler dead-man's switch (the reaper). Convergence, `recon_trades` proves G3
> (`ws_only = 0`, every `backfill_only` inside a manifest gap) over the reconcilable window;
> `make heal` backfills unhealed gap hours and re-proves it; silver contract tests gate gold; gold
> marts (`ohlcv_1m/1h/1d`, `volume_daily`, `liquidations_daily`) build from silver; the Athena seam
> test proves the silver merge behaviour on every push to `main`. QNT-466 ran the full sequence live
> end-to-end (stream → gap → heal → recon → manifest). Presentation (README, demo, dbt docs) is
> Phase 4.
> The target design is in [`docs/prd.md`](../prd.md) §5; this file describes only what is deployed
> or runnable today.

## Architecture

```
developer laptop ──► infra/bootstrap  (terraform, LOCAL state)
                        ├─ S3  hyperlake-tfstate-<account>   versioned, SSE, public-access blocked
                        ├─ DynamoDB hyperlake-tfstate-lock   pay-per-request
                        ├─ IAM OIDC provider + role hyperlake-github-actions
                        │     trust: repo:<github_repo>:*  ·  perms: athena/glue(bronze/silver/gold)/
                        │     s3(data bucket)/logs, full dbt-athena IAM set (QNT-473)
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
  hl-mainnet-node-data hourly/YYYYMMDD/H.lz4 ──► Step Functions Map (make backfill / make heal) ──►
      Lambda (per hour) stream LZ4 → filter watchlist (config/watchlist.yaml) → collapse fill pairs
      → Reservoir daily Parquet fallback (column-mapped, schema-pinned, loud on layout drift) if
        the official hour file is late/missing after 24h (QNT-465)                                │
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
                               gated by silver contract tests (schema/freshness/volume, QNT-464)
      bronze → RECON  recon_trades (ws_only / backfill_only / both over the reconcilable window;
                       manifest gaps seeded in), proves G3 (ADR-003), QNT-460
      silver → GOLD   ohlcv_1m/1h/1d · volume_daily · liquidations_daily (backfill-only), OHLCV
                       invariant tests; built only if silver's contract tests pass, QNT-463
      make heal SESSION=<id>  gap → covering hour list (+H+1) → not-landed check → re-run backfill
                       Map over just those hours → re-run dbt-run → re-run recon → flip healed:true
                       → commit manifest (QNT-461); QNT-466 proved the full stream→heal→recon cycle
                       live end-to-end on a real session
      make iceberg-maintain (expire snapshots / rewrite manifests)
                                                            Athena workgroup `hyperlake` (cloud) ·
                                                            DuckDB (local + CI, dbt --target duckdb)
                                                            seam_test schema: silver merge behaviour
                                                            proven on Athena on every push to main
                                                            (dbt-run.yml `seam` job, QNT-462)

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
                      Kinesis stream, writes a reap marker to S3, the only channel back to
                      session-down, which has no other way to learn a session was reaped

GitHub Actions
  ci.yml            on PR + push main, ZERO AWS creds: ruff · pyright · pytest · pip-audit
                     · dbt build --target duckdb · terraform fmt/validate · grep for long-lived AWS keys
                     (`make check` mirrors this exact step order for local runs)
  dbt-run.yml        `build` job: workflow_dispatch (run_key, select, target), OIDC creds, 20-min
                      timeout, uploads run_results.json, invoked by session-down, make heal, and
                      by hand (QNT-454). `seam` job: automatic on every push to main (also
                      dispatchable pre-merge via `-f job=seam`), runs the Athena seam test over
                      OIDC, the private repo's stand-in for a branch-protection required check
                      (QNT-462)
  ingester-image.yml  push-to-main (OIDC), builds + pushes the ingester image to ECR tagged by
                      commit SHA (QNT-457)
  tf-drift-check.yml  daily cron + workflow_dispatch (OIDC), compares the persistent layer's Glue
                      catalog against Terraform state (QNT-474); writes infra/main/backend.hcl from
                      repo variables on checkout, silently failed at `terraform init` on every
                      scheduled run for ~1 day until this step was added (PR #24, Phase 2 retro)

dbt/              two targets (ADR-002): duckdb (local/CI) · athena (OIDC, ap-northeast-1)
                  one macro `materialization_for_target` owns the seam
                  models/silver/trades.sql, Iceberg incremental merge on tid; contract-tested
                  (schema/freshness/volume, QNT-464); stg_trades_sample disabled on athena
                  (QNT-473, never wired to a real Glue source)
                  models/recon/recon_trades.sql, G3 set comparison over bronze, manifest gaps
                  seeded in (QNT-460)
                  models/gold/{ohlcv_1m,ohlcv_1h,ohlcv_1d,volume_daily,liquidations_daily}.sql,
                  built only if silver's contract tests pass; OHLCV invariant tests (QNT-463)
                  tag:seam models, seam_test schema fixtures proving the silver merge on Athena,
                  run by dbt-run.yml's `seam` job on every push to main (QNT-462)

src/hyperlake/    envelope (typed columns + raw_payload), watchlist loader, partition-name helper
                  (xyz:SP500 -> xyz_SP500), backfill/{hour_list,official,state_machine} (QNT-449/451/452),
                  backfill/reservoir.py (column-mapped fallback reader, schema-pinned, loud on
                  layout drift, QNT-465), ingester.py (WS subscribe, envelope, Kinesis PutRecords,
                  reconnect/gap, self-exit, QNT-456), session.py (manifest, cost estimate, overlap
                  guard, QNT-458), session_reaper.py (dead-man's-switch Lambda, QNT-459),
                  heal.py (gap_to_hours boundary-rule expansion, lookback_days sizing, QNT-461)

scripts/heal.py, scripts/recon.py, scripts/bronze_query.py  make heal (gap healing + re-recon,
                  QNT-461), make recon (per-session G3 check, QNT-460), make bronze-query
                  (bounded-`dt` ad-hoc query wrapper, refuses to run unfiltered, QNT-476)

sessions/         one committed manifest JSON per demo session (coins, start/end, deployed_sha,
                  gaps (with healed: true/false), dbt_runs, cost_estimate_usd, reaped/reaped_at),
                  lands QNT-458; QNT-466 produced the first fully populated manifest (gaps healed,
                  recon passing, real session)

costs/            sessions.csv log (cost_estimate_usd / cost_actual_usd / cost_status incl.
                  reaper-terminated) + `make cost-backfill` (Cost Explorer, filtered by the
                  `project` tag), real session rows from QNT-455/458/459 verification runs
```

## Components / layers

| Layer | Responsibility | Status |
|-------|----------------|--------|
| `infra/bootstrap/` | one-time, local-state: state backend, OIDC role, budget alarm + deny action, cost tag | deployed; Glue/S3 policy scoped to real db names (QNT-445, QNT-473) |
| `infra/main/persistent/` | data bucket, Glue databases bronze/silver/gold, Athena workgroup, ECR repo | deployed in ap-northeast-1 (QNT-450, QNT-457) |
| `infra/main/ephemeral/` | backfill Lambda + Step Functions; Fargate ingester + Kinesis + Firehose; session reaper Lambda + one-time schedule | applied/destroyed per session by `session-up`/`session-down` (QNT-451/452, QNT-457, QNT-459) |
| `dbt/` | two-target project: silver (contract-tested), recon (G3), gold marts, seam fixtures, materialization macro | duckdb builds in CI; athena runs via `dbt-run.yml` `build` job over OIDC (QNT-446/453/454/460/463/464), `seam` job auto on every push (QNT-462) |
| `src/hyperlake/` | shared package: envelope, watchlist, partition helper, backfill readers (official + Reservoir fallback), WS ingester, session manifest/cost/reaper, heal helpers | in use by the Lambda, Step Functions, Fargate task, session scripts, and `make heal` (QNT-449, QNT-456, QNT-458/459, QNT-461, QNT-465) |
| `scripts/spike/` | OQ-1 tid-parity gate (`capture_ws_trades.py`, `check_tid_parity.py`) | run once, gate passed 2026-09-05 (ADR-005) |
| `scripts/gh_run.sh` | dispatches `dbt-run.yml` by `run_key`, polls to completion, loud on failure/timeout | in use (QNT-454), called by `session-down`, `make heal`, `make recon` |
| `scripts/session_{up,down}.py` | session lifecycle: overlap guard, apply/destroy, manifest, cost estimate, dbt trigger | in use (QNT-458) |
| `scripts/heal.py` | `make heal SESSION=<id>`: gap → hour list, not-landed check, re-run backfill+dbt+recon, flip healed | in use (QNT-461), proved live end-to-end by QNT-466 |
| `scripts/recon.py` | `make recon`: per-session G3 check against `recon_trades` | in use (QNT-460), reused by `session-down` and `make heal` |
| `scripts/bronze_query.py` | `make bronze-query DT_FROM=...`: bounded ad-hoc Athena query wrapper, refuses unfiltered scans | in use (QNT-476), guards the cost class the QNT-476 investigation found |
| `sessions/` | one committed manifest per demo session, gaps with `healed` flag | real rows land per session (QNT-458); first fully-populated manifest (QNT-466) |
| `costs/` | session cost log schema + Cost Explorer backfill script | schema (QNT-447); real session rows land per session (QNT-458/459) |
| CI (`ci.yml`, `ingester-image.yml`, `tf-drift-check.yml`, `dbt-run.yml` `seam` job) | offline gate; SHA-tagged image build/push; nightly Glue-vs-state drift check; Athena seam test on every push | green; `tf-drift-check.yml` fixed mid-Phase-2 (backend.hcl, PR #24); caught the `seam_test` Glue db drift mid-Phase-3, fixed by QNT-477 |

## Data stores

- **bronze** (Glue db `bronze`, table `trades_raw`), plain Parquet, Hive partitions
  `coin=/dt=/source=`, partition projection (no crawler/MSCK). Written by the backfill Lambda
  (`source=backfill`) and, per session, by Firehose from the live WS ingester (`source=ws`);
  append-only, never pruned.
- **silver** (Glue db `silver`, table `trades`, Iceberg), merge on `tid`, `source_rank` picks
  `backfill` over `ws` on conflict, `first_seen_source` insert-only. Written only by `dbt-run.yml`
  over the OIDC role. Gated by contract tests (schema/freshness/volume, QNT-464) before gold builds.
- **recon** (`recon_trades`, over bronze), `ws_only`/`backfill_only`/`both` per `tid` per source
  over the reconcilable window (manifest gaps seeded in); proves G3 (`ws_only = 0`, every
  `backfill_only` inside a gap). Written by `dbt-run.yml`; re-run by `session-down` and `make heal`.
- **gold** (Glue db `gold`, Iceberg on athena / table on duckdb), `ohlcv_1m/1h/1d`, `volume_daily`,
  `liquidations_daily` (backfill-only by construction), derived from silver, windowed on event
  `time`. OHLCV invariant tests (`low ≤ open, close ≤ high`; candle volume = sum of trade `sz`).
- **seam_test** (Glue db, declared in Terraform + imported, QNT-477), dbt-athena fixture schema for
  the seam test; a handful of rows proving the silver merge behaviour on real Athena, not duckdb.

## External surfaces

- GitHub Actions → AWS via OIDC (`hyperlake-github-actions`), role ARN in repo variable
  `AWS_OIDC_ROLE_ARN`. Three workflow files exercise it for real: `dbt-run.yml` (`build` job:
  manual dispatch, or called by `session-down`/`make heal`; `seam` job: automatic on every push to
  main, QNT-462, the private repo's stand-in for a branch-protection required check),
  `ingester-image.yml` (auto on push to main), and `tf-drift-check.yml` (nightly cron + manual
  dispatch).
- The archive bucket (`hl-mainnet-node-data`, requester-pays, ap-northeast-1) is read by the
  backfill Lambda per invocation, one hour file at a time.
- Hyperliquid WS `trades` channel is reached by the live Fargate ingester for the duration of a
  session (QNT-456/457), and by the spike scripts from a developer laptop for one-off captures.

## Infrastructure

- Region `ap-northeast-1`; every resource carries `project=hyperlake` (provider `default_tags`).
  The `silver` Glue database's earlier hand-created/import-into-state history (QNT-473) is now
  covered going forward by `tf-drift-check.yml` (QNT-474, daily), which itself shipped silently
  broken at `terraform init` (missing `infra/main/backend.hcl` on a fresh checkout) for about a day
  before being caught and fixed (PR #24), see the Phase 2 retro's invariant/guard finding.
  `tf-drift-check.yml` then caught the same class of drift for real mid-Phase-3: QNT-462's `seam`
  job auto-created a `seam_test` Glue database dbt-athena never declared in Terraform; QNT-477
  imported it the same way QNT-473 did, see the Phase 3 retro and `docs/AC-templates.md`'s dbt
  model group, now requiring the Terraform declaration in the same PR as any new dbt-created schema.
- A cost investigation (2026-09-09) traced ~$0.62 of the project's ~$0.78 total spend at the time to
  unbounded ad-hoc Athena queries against `bronze.trades_raw` (partition projection issues an S3
  LIST per virtual partition with no `dt` bound). `make bronze-query DT_FROM=...` (QNT-476) now
  requires a bounded `dt` lower bound and refuses to run otherwise; see
  [`docs/guides/ops-runbook.md`](../guides/ops-runbook.md).
- Idle cost: S3 state + data buckets + DynamoDB on-demand lock table + Athena workgroup (no scan
  cost when idle) + ECR repo (image-storage only) ≈ under $1/month. Compute, Fargate ingester,
  Kinesis, Firehose, the session reaper, exists only inside a `session-up`/`session-down` window,
  bounded to `max_session_hours` (default 6) even if `session-down` is never run, by the reaper's
  one-time EventBridge Scheduler dead-man's switch (QNT-459). Recovery from a reaped session is
  documented in [`docs/guides/ops-runbook.md`](../guides/ops-runbook.md).
- Bootstrap runbook: [`docs/guides/bootstrap.md`](../guides/bootstrap.md).
