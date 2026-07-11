# Hyperlake — Product Requirements Document

| | |
|---|---|
| **Status** | Draft v0.3 — OQ-1 (source/region/window), OQ-6 (timeline), OQ-7 (bronze drift posture) remain; freezes after the Phase-1 spike |
| **Owner** | noahwins-ng |
| **Created** | 2026-07-09 |
| **Repo** | public (portfolio) |

---

## 1. Overview

**Hyperlake** is a streaming lakehouse for Hyperliquid (HyperCore) market data, built
entirely on AWS serverless services and reproducible from zero with one `terraform apply`.
It ingests live trades over WebSocket and backfills history from Hyperliquid's public S3
archives, landing both paths in the same Apache Iceberg tables on S3, transformed and
queried with Athena + dbt.

### Problem statement

This is a **portfolio project**. The "user problem" it solves is a hiring one: demonstrate,
in a single public repo a hiring manager can absorb in ten minutes, competence across the
modern data engineering core — streaming ingestion, lakehouse table formats, batch/stream
convergence, IaC, orchestration, data quality, and cost discipline — on a real, high-volume,
non-toy dataset.

The secondary problem is cost: portfolio infra that runs 24/7 bleeds money and rots. Hyperlake
is **ephemeral by design** — built up for a recorded demo or interview walkthrough, torn down
after, with idle cost near zero.

### Why Hyperliquid data

- Real production-scale volume (millions of trades/day) — not a Kaggle CSV.
- Two genuinely different acquisition paths (live WebSocket + requester-pays S3 archive),
  which forces the batch/stream convergence problem that makes lakehouse design interesting.
- Public, free, and no API key gatekeeping for the core feeds.

## 2. Goals

| # | Goal | Success metric |
|---|------|----------------|
| G1 | One-command reproducibility | documented bootstrap + `terraform apply` → queryable tables in Athena in < 15 min, on any AWS account |
| G2 | Streaming path works | Live trades visible in bronze < 60 s after emission during a demo session |
| G3 | Batch/stream convergence | Backfilled and streamed rows land in the **same** silver tables; verified by **replay reconciliation** — stream an hour live, later backfill the same hour from the archive, dbt test proves 0 missing / 0 duplicate trades |
| G4 | Cost discipline | < $2 per demo session; < $2/month idle; every run's cost recorded in the repo |
| G5 | Data quality is enforced, not claimed | dbt tests + freshness/volume checks gate the gold layer; failures visible in the demo |
| G6 | Legible to a recruiter | README with architecture diagram, recorded demo, and per-layer sample queries; PRD/ADRs show product thinking |

## 3. Non-goals

Explicitly out of scope — reject in review if it creeps in:

- **No trading, signals, or execution.** Market data engineering only; no order placement,
  no wallets, no keys with financial power.
- **No 24/7 operation.** No always-on dashboard, no uptime SLO. The artifact is the repo +
  a recorded demo, not a live service.
- **No self-hosted Hyperliquid node.** The node requires x86 / 32 GB RAM / ~20 GB logs/day —
  a different project. WebSocket API + S3 archives only.
- **No paid data sources.** Free feeds and requester-pays archive transfer only.
- **No BI product.** Athena/DuckDB queries and dbt docs are the presentation layer; no
  QuickSight, no custom frontend.
- **No multi-cloud.** AWS only, one region (co-located with the archive bucket).

## 4. Audience

1. **Hiring managers / recruiters** (primary) — skim README, diagram, maybe one workflow file.
2. **Data engineers** (interviewers) — read the Terraform, the dbt models, the convergence
   logic; judge the trade-offs.
3. **The owner** — learning vehicle for AWS-native streaming (Kinesis) and Iceberg. **AWS
   fluency is beginner**: every design choice biases toward fewer, simpler managed services,
   and the first ticket of each phase is a bounded learning spike on that phase's new services.

## 5. Architecture

```
 ┌─ LIVE PATH (demo sessions only) ──────────────────────────────┐
 │  Hyperliquid WS API ──► Fargate ingester ──► Kinesis Data     │
 │  (trades, selected     (0.25 vCPU task)      Streams          │
 │   coins)                                     (on-demand)      │
 │                                                 │             │
 │                                            Firehose ──► S3    │
 └─────────────────────────────────────────────────┬─────────────┘
                                                   ▼
 ┌─ BATCH PATH (backfill) ───────────┐      BRONZE (plain Parquet, raw)
 │  Hyperliquid / Reservoir S3       │            │
 │  archives (requester-pays) ──►    │      dbt-athena transforms
 │  Lambda backfill jobs ────────────┘            │
 │  (EventBridge / Step Functions)         SILVER (Iceberg: typed, deduped,
 └───────────────────────────────────┐      batch+stream merged)
                                     │            │
                                     ▼      GOLD (Iceberg marts: OHLCV,
                                Athena / DuckDB    volume)
```

- **Ingestion (live):** a single containerized WebSocket consumer on Fargate (Lambda cannot
  hold sockets past 15 min). Runs only during demo sessions.
- **Buffer:** Kinesis Data Streams, on-demand mode. Kafka-compatible alternatives (MSK
  Serverless) rejected on cost — see cost model.
- **Landing:** Firehose converts to Parquet natively (against a Glue schema) and
  micro-batches to S3. Bronze is append-only **plain Parquet** with Hive-style partitions —
  deliberately *not* Iceberg, so nothing outside Athena ever needs an Iceberg writer.
- **Backfill:** plain-Python Lambda jobs (no Glue Spark ETL — one less paradigm) read the
  requester-pays archive and write the identical bronze Parquet layout.
- **Table format:** silver/gold are Apache Iceberg tables in the **Glue catalog**, created
  and written exclusively through dbt-athena — one Iceberg write path, owned by dbt.
- **Transforms:** dbt-athena; medallion bronze → silver → gold. Dedup and the batch/stream
  seam are resolved at silver.
- **Orchestration:** EventBridge schedules + Step Functions. No Airflow/MWAA (cost).
- **IaC:** Terraform, single root module, `apply`/`destroy` as the demo lifecycle. Optional
  persistent layer (S3 data + catalog) separated from the ephemeral layer (compute, streams)
  so data can survive teardown when desired.

### Data model (draft — freezes at the end of the Phase-1 spike)

- **Bronze — `trades_raw`** (plain Parquet, partitioned `coin=<market>/dt=<utc-date>`, where
  `dt` is derived from the **exchange event time**, not arrival time — see NFR-5): the
  raw event as received, plus lineage columns `source` (`ws` | `backfill`), `ingested_at`,
  `session_id`. Backfill and stream write the identical layout.
- **Silver — `trades`** (Iceberg): one row per trade, exactly-once. Typed columns: `tid`
  (trade id — the dedup key), `coin`, `side`, `px` (decimal), `sz` (decimal), `time` (UTC
  timestamp), liquidation/crossed flags if the source provides them, `source`. Built by dbt
  incremental merge on `tid`; late or replayed data upserts cleanly.
- **Gold** (Iceberg marts): `ohlcv_1m` / `ohlcv_1h` / `ohlcv_1d` per coin, `volume_daily`.
  Liquidation marts only if the chosen backfill source carries the flag (OQ-1 spike criterion).

### Ingestion contract & failure modes

Contract: **at-least-once into bronze, exactly-once at silver** (dedup on `tid`).

| Failure | Behavior |
|---|---|
| WS disconnect mid-session | Reconnect with backoff; gap recorded in the session manifest, healed later by an archive replay of the affected window |
| Duplicate delivery (reconnect, Kinesis retry) | Expected; resolved at silver by the `tid` merge |
| Late / out-of-order events | Absorbed by the silver incremental merge; OHLCV rebuilds affected windows |
| Firehose delivery failure | Failed records land under an S3 `errors/` prefix; CloudWatch alarm active during sessions |
| Archive source unavailable (Reservoir is a third-party public good) | Fall back to the official `hl-mainnet-node-data` bucket; both readers share the bronze layout |

## 6. Requirements

### Functional

- **FR-1** Ingest live trades for a configurable coin list from the HyperCore WebSocket API
  into bronze via Kinesis/Firehose.
- **FR-2** Backfill a configurable historical window from the S3 archive into the same
  bronze layout. Default window: **30 days**; extended only if the Phase-1 volume
  measurement prices a longer window under ~$10 (transfer + storage).
- **FR-3** Silver layer: typed, schema-enforced, exactly-once-per-trade (dedup on trade id),
  with the batch/stream seam explicitly handled and tested.
- **FR-4** Gold layer: at minimum OHLCV candles and daily volume/liquidation marts.
- **FR-5** All layers queryable in Athena; sample queries committed in the repo.
- **FR-6** One-command build (`terraform apply` + a documented make target) and teardown
  (`terraform destroy`) with a checklist proving nothing billable is left behind.
- **FR-7** Per-run cost captured (Cost Explorer or tagged-resource report) and committed to
  a `costs/` log in the repo.
- **FR-8** Session lifecycle is scripted and first-class: `make session-up` /
  `make session-down` bring the ephemeral layer up/down and write a **session manifest**
  (coins, start/end, gaps, per-run cost) committed to the repo.
- **FR-9** Local development works without AWS: dbt models testable against DuckDB with
  committed sample Parquet fixtures; ingester unit-tested against recorded WS fixtures.
  CI (GitHub Actions) runs pytest + dbt-duckdb build + `terraform fmt/validate` on every PR
  — free, no cloud credentials needed.

### Non-functional

- **NFR-1 Cost:** hard budget $15/month during active development; AWS Budgets alarm at $10
  provisioned by Terraform on day one. The alarm is a **lagging backstop** (Budgets data
  refreshes on a multi-hour delay, typically 8–12 h) — the primary guardrails are the
  scripted `session-down` (FR-8) and the post-destroy checklist (FR-6).
- **NFR-2 No bill-surprise services:** NAT Gateway, MWAA, MSK provisioned, OpenSearch, and
  QuickSight are banned. Fargate runs with a public IP in a public subnet.
- **NFR-3 Security:** no long-lived AWS keys in the repo; GitHub Actions uses OIDC; secrets
  (if any) via SOPS, following the pattern already debugged in the owner's prior projects.
- **NFR-4 Reproducibility:** a stranger with an AWS account must be able to reproduce the
  stack from the README alone.
- **NFR-5 Timestamps:** UTC everywhere in storage; rendering concerns don't exist (no UI).
  Partitioning (`dt=`) and every gold time window derive **exclusively from exchange event
  time (`time`)**, never `ingested_at`/arrival time — otherwise the streamed hour and the
  backfilled hour disagree at bucket boundaries and G3 reconciliation shows phantom gaps.
- **NFR-6 Process/code hygiene:** conventional commits; PRD → ADRs for every significant
  decision (this file's open questions each terminate in an ADR).
- **NFR-7 Terraform state bootstrap:** a small documented one-time `bootstrap/` step (local
  state) creates the S3 state bucket, lock table, OIDC role, and budget alarm; the main
  stack then uses the S3 backend. G1's 15-minute claim includes this step.

## 7. Phases

| Phase | Deliverable | Proves |
|-------|-------------|--------|
| **0 — Scaffold** | Repo, Terraform bootstrap (state, budget alarm, OIDC), CI skeleton | Cost guardrails exist before any resource does |
| **1 — Lakehouse (batch)** | Spike: measure archive volume + schema (decides OQ-1, region, backfill window) → Lambda backfill → bronze Parquet → silver Iceberg via dbt → Athena queries | Lakehouse fundamentals, requester-pays handling |
| **2 — Streaming** | Fargate ingester → Kinesis → Firehose → same bronze | Streaming ingestion, live demo capability |
| **3 — Convergence + transforms** | dbt silver/gold, dedup at the seam, data quality tests | The actual hard problem; the interview talking point |
| **4 — Presentation** | README + diagram, recorded demo, cost report, dbt docs | Legibility to the hiring audience |

Phases ship sequentially; each ends with a working, demoable state and a teardown test.

## 8. Cost model (approximate, us-east-1 list prices)

| Item | Basis | Per ~4 h demo |
|------|-------|---------------|
| Fargate ingester (0.25 vCPU / 0.5 GB) | ~$0.012/hr | ~$0.05 |
| Kinesis on-demand | $0.04/hr + $0.08/GB | ~$0.20 |
| Firehose | ~$0.03/GB | pennies |
| Lambda | negligible at this scale | ~$0 |
| Athena | $5/TB scanned | pennies |
| Backfill Lambda + Athena CTAS | negligible at this scale | ~$0.10 |
| **Session total** | | **≈ $1–2** |

One-time backfill: requester-pays GETs + cross-region transfer (~$0.09/GB if not co-located)
→ deploy in the archive bucket's region; expect $5–15 worst case. Idle: S3 storage only,
~$0.50–1.50/month for 20–50 GB Parquet. No free-tier credits assumed (account's credits are
exhausted) — the budget alarm (NFR-1) is the sole guardrail.

## 9. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Teardown misses a billable resource | Silent monthly burn | Everything in one Terraform state; budget alarm; post-destroy checklist (FR-6) |
| WS feed schema drift / undocumented changes | Broken ingester mid-demo | Bronze stores raw payloads; schema enforcement deferred to silver; contract tests. Note: naive Firehose→Parquet contradicts this (drifted records die at conversion) — resolved by OQ-7 |
| Archive format differs from WS format | Convergence complexity explodes | Prototype both readers in Phase 1 spike before freezing the bronze schema (OQ-1) |
| Kinesis/Fargate left running after a session | ~$30+/month | Session start/stop is a scripted pair; destroy is part of the demo script, not an afterthought |
| Free-tier credit assumptions wrong | Unplanned spend | Budget alarm at $10 is independent of credits |
| Scope creep toward a "product" (dashboards, alerts, 24/7) | Never ships | Non-goals section; PRD freeze after OQs resolve |
| AWS-beginner missteps (IAM, VPC, billing) | Slow phases, surprise config | Per-phase learning spikes; no-VPC-by-default (public-subnet Fargate only); budget alarm from day 0; everything in Terraform so mistakes are reviewable and reversible |
| Publishing raw data from a public repo | ToS / redistribution questions | Commit only small derived samples (gold marts) as fixtures; link to sources for raw data |

## 10. Settled decisions

Firmed in the 2026-07-09 planning discussion (pre-freeze; each significant one still gets a
short ADR when its phase starts):

- **Name:** `hyperlake` (collision-checked on GitHub; no affiliation claim on "Hyperliquid").
- **Platform:** AWS serverless, ephemeral apply/destroy — chosen over VPS (shared or
  dedicated) for cloud-native hiring signal and near-zero idle cost.
- **AWS account:** owner's existing account — hosts nothing else, so a dedicated IAM role +
  `project=hyperlake` tags suffice (no permission-boundary complexity). Free-tier credits
  already exhausted: the cost model stands on list prices alone; the budget alarm is the
  guardrail.
- **Tracker:** Linear, Quant team (same as sibling projects); process stays private, the
  public repo carries the polish.
- **Watchlist (resolves OQ-3):** 5 markets — BTC, ETH, HYPE (core perps) + S&P 500 and
  XYZ100 (HIP-3 builder-deployed markets), config-driven so widening is trivial.
  Estimated ~0.5–0.8M trades/day if run 24/7 (~100–300 MB/day raw) — trivial for Kinesis
  on-demand, and ingestion only runs during demo sessions. HIP-3 markets use
  deployer-prefixed naming on the WS API and differ in archive coverage — verify in the
  OQ-1 spike. Mixing core + HIP-3 market types is a deliberate feature (heterogeneous
  sources through one pipeline).
- **Ingester language:** Python.
- **License:** MIT *(resolves OQ-5; 2026-07-10)*.
- **Catalog:** AWS Glue catalog; S3 Tables parked *(resolves OQ-2; 2026-07-10 — beginner
  AWS fluency + dbt-athena maturity outweigh the newer resume signal)*.
- **Write-path simplification (2026-07-10):** bronze is plain Parquet (Hive partitions);
  Iceberg exists only at silver/gold and is written only by dbt-athena. Backfill uses plain
  Python Lambda, never Glue Spark ETL. Chosen to minimize new-service surface for a
  first AWS project.

## 11. Open questions

Each resolves to an ADR before its dependent phase starts.

- **OQ-1 — Backfill source + region:** Hydromancer Reservoir (free, trades-focused,
  advertises HIP-3 coverage) vs. official `hl-mainnet-node-data` (requester-pays, richer:
  fills, transfers, funding; **ap-northeast-1**, while `hyperliquid-archive` is us-east-1).
  The deploy region co-locates with the chosen bucket, so source and region are one
  decision. Spike criteria: **measure per-day volume for the watchlist** (prices the FR-2
  backfill window — currently unknown), schema fit vs. the WS trade format, HIP-3 market
  coverage (watchlist includes HIP-3 indices), transfer cost. One criterion is a hard
  **pass/fail gate: trade-identity parity** — the archive must carry the same `tid` the WS
  feed emits for the same trade. The silver exactly-once contract and the G3 replay
  reconciliation both dedup on `tid`; if the archive lacks it or uses a different identity,
  the dedup key reopens (composite keys like `coin,time,px,sz,side` collide on simultaneous
  identical trades) and G3 must be re-scoped **before** the bronze schema freezes.
  *(Blocks Phase 1.)*
- **OQ-4 — Demo artifact:** recorded video vs. scripted live run vs. both. *(Blocks Phase 4.)*
- **OQ-7 — Bronze drift posture:** the risk table defers schema enforcement to silver
  ("bronze stores raw payloads"), but Firehose's native Parquet conversion validates
  against a fixed Glue schema — a drifted record fails conversion and lands in `errors/`,
  making bronze the de-facto enforcement point and silently starving silver mid-demo.
  Leading option: an **ingester-owned envelope** — the ingester emits best-effort typed
  columns plus a `raw_payload` JSON string column, so the Firehose schema is owned by our
  ingester rather than by Hyperliquid, and source drift degrades to null typed fields
  instead of lost records (backfill Lambda writes the same envelope). Alternative:
  Firehose lands raw JSON and a Lambda does the Parquet conversion. Resolve as an ADR.
  *(Blocks Phase 2.)*
- **OQ-6 — Timeline:** target date for README-complete (job-hunt driven?). Until answered,
  assume no hard deadline and size phases at ~1–2 focused weekends each (beginner-AWS pace).

## 12. Parking lot

Explicitly parked — reopen the PRD before building any of these:

- Schema-evolution demo (add a column at silver via Iceberg evolution — the strongest
  stretch goal if Phase 3 lands early; it's Iceberg's headline feature)
- L2 order-book data (`hyperliquid-archive` market_data), funding rates, HyperEVM data
- All-markets scope (watchlist is config-driven; widening is a config change, not a build)
- S3 Tables migration; MSK/Kafka variant
- Live always-on dashboard (violates ephemerality by design)

## 13. References

- Hyperliquid historical data docs — https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data
- Hyperliquid WebSocket API — https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket
- Hydromancer Reservoir archive — https://hydromancer.xyz/historical-data
- Owner's prior ops patterns (SOPS, deploy, health checks) — private repos `equity-data-agent`, `argus-agent`
