# Hyperlake — Requirements

The spec: *what* we're building and *why*, organized by phase. This is the source of intent;
`project-plan.md` tracks execution against it. `change-scope` edits this file when scope shifts.

> Keep entries to the what + the why + hard constraints. Implementation detail lives in code and
> `architecture/system-overview.md`, not here.

> **Source PRD:** [`docs/prd.md`](prd.md) (Draft v0.8) is the anchor for scope, architecture,
> cost model, and open questions. Phases below were decomposed from its §7 by `flow-plan-project`
> on 2026-09-04; each phase maps to a Linear milestone in project **Hyperlake** (team Quant).

## Non-negotiables (from PRD §3, §5, §6)

- No trading, signals, or execution — market data engineering only.
- Ephemeral by design: no 24/7 operation; < $2 per demo session, < $2/month idle.
- Bronze is plain Parquet (Hive partitions); Iceberg only at silver/gold, written only by dbt-athena.
- Backfill is plain-Python Lambda; no Glue Spark. Banned: NAT Gateway, MWAA, MSK provisioned, OpenSearch, QuickSight.
- At-least-once into bronze, exactly-once at silver (dedup on `tid`).
- UTC everywhere; partitions and gold windows derive from exchange event `time`, never arrival time.
- Every AWS resource Terraform-managed and tagged `project=hyperlake`; OIDC only, no long-lived keys.
- Watchlist is config-driven. Region `ap-northeast-1`.

## Phase 0 — Scaffold

**Goal:** Cost guardrails and the toolchain exist before any data resource does.

- **Toolchain + CI** — ruff, pyright, pytest, `terraform fmt/validate` on every PR with no cloud credentials. *Why:* FR-9 free, offline CI; the profile's verify gates depend on it.
- **Terraform bootstrap** — one-time local-state step creating the S3 state bucket, lock table, GitHub OIDC role, Budgets alarm ($10) and a create-deny action ($15); activate the `project` cost allocation tag. *Why:* NFR-1, NFR-3, NFR-7; tag activation takes up to 24 h so it must precede the first billable resource. *Constraints:* persistent/ephemeral split in the main stack from day one.
- **Two-target dbt skeleton** — `duckdb` and `athena` targets switched by one macro; builds on DuckDB in CI. *Why:* ADR-002; every later model lands on a working seam.
- **Costs log** — `costs/` schema with `cost_estimate` / `cost_actual` and a `make cost-backfill` that fills actuals from Cost Explorer the next day. *Why:* G4, FR-7; the history must start at session 1.

## Phase 1 — Lakehouse (batch)

**Goal:** Archive → Lambda backfill → bronze Parquet → silver Iceberg via `dbt-run` → queryable in Athena.

- **Close OQ-1** — `tid` parity between a live WS capture and the official hour file; ADR-005. *Why:* the bronze schema freezes on this; blocks the rest of the phase. *Constraints:* plan B is `hash` + `coin, side, px, sz`.
- **Shared package** — envelope schema (typed columns + `raw_payload`), watchlist config, partition-name helper (`xyz:SP500` → `xyz_SP500`), `dt` from event time. *Why:* OQ-7 and NFR-5; ingester and backfill must emit the identical grain and layout.
- **Persistent layer** — data bucket, Glue database, bronze `trades_raw` via partition projection, Athena workgroup with a scan cap. *Why:* no crawler / `MSCK`; data outlives a session.
- **Backfill Lambda** — one invocation per official hour file: stream LZ4, filter watchlist, collapse fill pairs, write bronze at deterministic keys. *Why:* FR-2; measured ~$1 for 30 days. *Constraints:* hour files are cut by arrival time (fetch H and H+1); re-runs overwrite.
- **Fan-out** — Step Functions Map over an hour list + `make backfill`; 1-day sample timed for G1. *Why:* ADR-001 scopes orchestration to this; `heal` reuses it.
- **Silver merge** — Iceberg incremental merge on `tid`, `dt` lookback, `backfill` outranks `ws`, insert-only `first_seen_source`; sample queries. *Why:* FR-3, ADR-002/003.
- **`dbt-run` workflow** — GitHub Actions over OIDC with the `run_key` completion contract and a 20-min timeout; `make iceberg-maintain`. *Why:* ADR-001; failures must be loud and land in the manifest.

## Phase 2 — Streaming

**Goal:** Fargate ingester → Kinesis → Firehose → the same bronze; scripted session lifecycle; bounded blast radius.

- **Landing spike** — Kinesis + Firehose Parquet conversion and event-time dynamic partitioning, then destroy. *Why:* PRD §4 per-phase learning spike; ADR-004 go/no-go.
- **WebSocket ingester** — `trades` subscription, envelope, `PutRecords` with latency logging, reconnect with gap recording, `--max-session-hours` self-exit. *Why:* FR-1, G2(a). *Constraints:* feed replays a tail on connect; manifest `start` is the first trade ≥ connect time.
- **Fargate + Firehose in the ephemeral layer** — default VPC, public IP, egress-only SG; image tagged by commit SHA; G2(b) emission→bronze < 3 min measured. *Why:* NFR-2 forbids our own VPC plumbing.
- **Session lifecycle** — `make session-up` / `session-down` with a committed manifest (coins, start/end, gaps, dbt runs, `cost_estimate`, `cost_actual: pending`). *Why:* FR-8; session-down is the primary cost guardrail.
- **Session reaper** — one-time EventBridge Scheduler + Lambda bounding a forgotten session to ~6 h; apply/destroy verified after a real reap. *Why:* FR-8 dead-man's switch; Terraform drift must be proven tolerable.

## Phase 3 — Convergence + transforms

**Goal:** G3 proven at bronze, gaps healed, seam tested on Athena, gold marts gated by data-quality tests.

- **`recon_trades`** — distinct `tid` per source over the reconcilable window; tests `ws_only = 0` and every `backfill_only` inside a manifest gap. *Why:* ADR-003 — this pair of assertions is G3.
- **`make heal`** — backfill unhealed gap hours plus the following hour, re-run dbt, flip `healed`. *Why:* FR-8; makes G3 hold for sessions with disconnects.
- **Athena seam test** — `seam_test` schema, three ordering cases, on every push to `main`. *Why:* ADR-002 amendment; merge behaviour cannot be proven on DuckDB.
- **Gold marts** — OHLCV 1m/1h/1d, `volume_daily`, `liquidations_daily` (backfill-only) with invariant tests. *Why:* FR-4, G5, NFR-5.
- **Silver contracts** — enforced schema, freshness, gold gated on silver tests, a demo-fail target. *Why:* G5 — enforced, not claimed.
- **Reservoir fallback** — column-mapped reader for the daily Parquet, layout-pinned, loud on drift. *Why:* failure mode "archive hour missing after 24 h"; Reservoir has reorganised once already.
- **End-to-end G3 replay** — a real session with an induced disconnect, healed and reconciled; manifest committed. *Why:* the phase's demoable state.

## Phase 4 — Presentation

**Goal:** A hiring manager can absorb the project in ten minutes; a stranger can reproduce it.

- **README** — diagram, per-layer sample queries, bootstrap path, timed G1 (< 15 min). *Why:* G1, G6, NFR-4.
- **Demo artifact** — `docs/demo-runbook.md` with real measured timings, the G3 recon walkthrough, and the data-quality failure sidebar (`make dbt-demo-fail`). *Why:* PRD §7 Phase 4; OQ-4 (amended 2026-09-15) — the recorded-video requirement was dropped in favor of this runbook, which already carries the same evidence as literal, reproducible command + output rather than a recording.
- **Cost report** — `make cost-report` renders `costs/sessions.csv`, asserts per-session and idle spend stay under the PRD G4 ceiling, and reconciles the Cost Explorer all-time total (`project=hyperlake` tag) against `sum(cost_actual)` in `costs/sessions.csv`, explaining any gap. *Why:* G4 evidence. The reconciliation guards against `costs/sessions.csv` looking clean while non-session-scoped spend (e.g. the ad-hoc Athena query cost QNT-476 found and fixed) goes unreported.
- **dbt docs generated in CI, lineage graph in the README** — `dbt docs generate --target duckdb` on every push to `main` keeps the manifest/catalog current; the lineage graph is captured as a static screenshot for the README rather than hosted on GitHub Pages (scope change 2026-09-16, QNT-470 amended) — GitHub Pages cannot serve from a private repository on the GitHub Free plan, and the repo stays private. *Why:* G6; the presentation layer without a BI product.

## Ops & Reliability (perpetual)

- **Post-destroy audit** — script listing any live `project=hyperlake` billable ephemeral resources; fails loudly; last step of `session-down`. *Why:* FR-6; a checklist is not a guard.
- **Terraform state drift check** — compares live `project=hyperlake` resources (at minimum Glue databases/tables) against `terraform state list` on the persistent layer; fails loudly on a resource in either direction with no counterpart in the other. *Why:* Non-negotiables — "every AWS resource Terraform-managed... nothing hand-created"; QNT-473 (Phase 1 retro) found the `silver` Glue database had drifted (hand-created, never `terraform apply`'d) undetected through most of the phase.

<!-- Add phases as the roadmap grows. A phase maps to a Linear milestone. -->
