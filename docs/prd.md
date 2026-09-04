# Hyperlake — Product Requirements Document

| | |
|---|---|
| **Status** | Draft v0.8 — ops gaps settled (Athena seam test, workflow completion contract, estimate-vs-actual cost reporting, session reaper built). v0.7: seam gaps closed (gap-aware G3 test, merge source precedence, idempotent backfill keys, `make heal`, partition projection + name normalisation). OQ-1 narrowed to the `tid`-parity check only; region, volume, grain, HIP-3 coverage and backfill cost measured in the [2026-09-04 desk spike](spikes/2026-09-04-oq1-archive-desk-spike.md). Freezes once `tid` parity passes. (v0.5 same day: G3 proof at bronze, G1/G2 re-scoped, dbt runtime + FR-9 split, ADR-001..004) |
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

- Real production-scale volume — measured ~6.6 M trades/day network-wide across ~440
  markets, ~1.1 M/day on the default 5-market watchlist — not a Kaggle CSV. Widening to
  all markets is a config change on the same code path.
- Two genuinely different acquisition paths (live WebSocket + requester-pays S3 archive),
  which forces the batch/stream convergence problem that makes lakehouse design interesting.
- Public, free, and no API key gatekeeping for the core feeds.

## 2. Goals

| # | Goal | Success metric |
|---|------|----------------|
| G1 | One-command reproducibility | documented bootstrap + `terraform apply` + a **one-day sample backfill** → queryable tables in Athena in < 15 min, on any AWS account. The full FR-2 backfill window is timed separately and recorded in the session manifest — it is not part of the 15-minute claim |
| G2 | Streaming path works | Two measurements, both during a demo session: (a) **emission → Kinesis < 5 s**, from the ingester's logged `PutRecords` latency; (b) **emission → visible in bronze < 3 min**, from a query on `ingested_at`. The bronze number is bounded below by Firehose's 60 s buffer interval + Parquet conversion — see Architecture → Landing |
| G3 | Batch/stream convergence | Backfilled and streamed rows land in the **same** silver tables; verified by **replay reconciliation at bronze** — stream an hour live, later backfill the same hour from the archive, and a dbt test over bronze proves **`ws_only = 0`** and **every `backfill_only` trade falls inside a gap interval recorded in the session manifest** (a WS disconnect legitimately produces archive-only trades; anything outside a recorded gap is a real miss). The window covers only hours whose archive file has landed (`recon_trades`; [ADR-003](decisions/ADR-003-g3-reconciliation-at-bronze.md)) |
| G4 | Cost discipline | < $2 per demo session; < $2/month idle; every session's `cost_estimate` and next-day `cost_actual` committed to `costs/`; a forgotten session is bounded to ~6 h by the session reaper (FR-8) |
| G5 | Data quality is enforced, not claimed | dbt tests gate the gold layer and failures are visible in the demo. **Freshness** = max event `time` in silver is within the session window; **volume** = the `recon_trades` counts (G3 and G5 share the same test). Plus schema/uniqueness/not-null tests on silver and OHLCV invariants on gold (`low ≤ open,close ≤ high`, candle volume = sum of trades) |
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
- **No multi-cloud.** AWS only, one region — **ap-northeast-1**, where both archive
  buckets live.

## 4. Audience

1. **Hiring managers / recruiters** (primary) — skim README, diagram, maybe one workflow file.
2. **Data engineers** (interviewers) — read the Terraform, the dbt models, the convergence
   logic; judge the trade-offs.
3. **The owner** — learning vehicle for AWS-native streaming (Kinesis) and Iceberg. **AWS
   fluency is beginner**: every design choice biases toward fewer, simpler managed services,
   and the first ticket of each phase is a bounded learning spike on that phase's new services.

## 5. Architecture

```
 AWS ap-northeast-1 · Terraform · ephemeral layer = compute + streams, persistent = S3 + Glue

 ┌─ LIVE PATH (demo sessions only) ────────────────────────────────────────────┐
 │  Hyperliquid WS API ──► Fargate ingester ──► Kinesis Data Streams           │
 │  trades: BTC ETH HYPE   (0.25 vCPU; wraps     (on-demand; replay buffer)    │
 │  xyz:SP500 xyz:XYZ100    each trade in the           │                      │
 │  ~17 trades/s            ingester envelope)          ▼                      │
 │                                          Firehose 60 s / 64 MB → Parquet    │
 │                                          dynamic partition coin=/dt=(time)  │
 └──────────────────────────────────────────────────────┬──────────────────────┘
                                                        ▼
 ┌─ BATCH PATH (backfill, per hour file) ──────┐   BRONZE  trades_raw
 │  hl-mainnet-node-data  (primary, ~1 h lag)  │   plain Parquet · coin=/dt=
 │   node_fills_by_block/hourly/YYYYMMDD/H.lz4 │   1 row per tid per source
 │  hydromancer-reservoir (fallback, daily)    │   append-only · never pruned
 │           │                                 │            │
 │  EventBridge ► Step Functions ► Lambda ×1   │            │
 │  per hour file: stream LZ4 → filter         │            │
 │  watchlist → collapse fill pair → write ────┼────────────┤
 └─────────────────────────────────────────────┘            │
                                                            ▼
 ┌─ TRANSFORMS  GitHub Actions `dbt-run` (OIDC) → dbt-athena ───────────────────┐
 │  bronze ──► SILVER trades   Iceberg, Glue catalog, merge on tid,             │
 │             dt lookback, first_seen_source                                   │
 │  silver ──► GOLD ohlcv_1m / ohlcv_1h / ohlcv_1d · volume_daily               │
 │  bronze ──► recon_trades  ws_only / backfill_only / both  = G3 proof         │
 │  triggered by: backfill runner · make session-down (then OPTIMIZE/VACUUM)    │
 └──────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
                     Athena (cloud)  ·  DuckDB (local dev + CI, target=duckdb)
```

- **Ingestion (live):** a single containerized WebSocket consumer on Fargate (Lambda cannot
  hold sockets past 15 min). Runs only during demo sessions, in the **default VPC** with a
  public IP and an egress-only security group — no subnets, NAT, or endpoints of our own.
  **Confirmed (2026-09-04 capture):** the `trades` subscription replays recent trades on
  connect (first message carried a trade ~6 s older than the connect time), so the
  session manifest's `start` is the first trade `time` ≥ connect time; earlier replayed
  trades are kept (silver dedups them) and every reconnect re-delivers a short tail.
- **Buffer:** Kinesis Data Streams, on-demand mode, **partition key = `coin`** so ordering
  holds per market. Kafka-compatible alternatives (MSK Serverless) rejected on cost — see
  cost model.
- **Landing:** Firehose converts to Parquet natively (against a Glue schema) and
  micro-batches to S3. Bronze is append-only **plain Parquet** with Hive-style partitions —
  deliberately *not* Iceberg, so nothing outside Athena ever needs an Iceberg writer.
  - **Why not a direct Fargate → S3 write:** Kinesis is the replay buffer, Firehose owns
    buffering/rotation/Parquet conversion; cost is pennies either way
    ([ADR-004](decisions/ADR-004-kinesis-firehose-over-direct-write.md)).
  - **Buffer floor (drives G2):** Parquet conversion forces a 64 MB minimum buffer, so at
    watchlist volume every flush is interval-driven. Configured **60 s / 64 MB**; expect
    ~60–120 s emission→S3.
  - **Partitioning:** the `coin=/dt=` layout comes from Firehose **dynamic partitioning** —
    a JQ expression over the envelope's `coin` and `time` (epoch ms → UTC date), never over
    arrival time (NFR-5). Dynamic partitioning is a separately-billed Firehose feature
    (~$0.02/GB + per-object) — included in the cost model.
  - **Partition value normalisation:** HIP-3 markets are named `xyz:SP500`; a colon in a
    Hive partition value is awkward in Glue, Athena, and Firehose JQ keys. The `coin`
    column keeps the exact name; the partition value is the name with `:` → `_`
    (`coin=xyz_SP500`). One helper owns the mapping, used by ingester and backfill alike.
  - **Partition registration:** bronze uses **Glue partition projection** (`coin` as an
    enum from the watchlist config, `dt` as a date range) — no crawler, no `MSCK REPAIR`,
    no per-partition writes to the catalog. New partitions are queryable the moment
    objects land.
  - **Small files are expected:** one Parquet object per coin per ~minute per session.
    Fine at this scale for Athena; compaction happens at silver by construction (the
    Iceberg merge rewrites into few, large files). No bronze compaction job.
- **Backfill:** plain-Python Lambda jobs (no Glue Spark ETL — one less paradigm) read the
  requester-pays archive and write the identical bronze Parquet layout. **Fan-out unit is
  one archive hour file** (official layout: every market in one ~46 MB LZ4 / ~235 MB JSON
  file per hour; 720 invocations for 30 days). The Lambda stream-decodes LZ4, filters the
  watchlist, collapses fill pairs to trades, and writes per-coin partitions. Reservoir
  fallback uses the same shape with daily Parquet files and a column-mapping layer.
  **Idempotent by object key:** each invocation writes exactly one object per coin at a
  deterministic key, `coin=<c>/dt=<d>/source=backfill/hour=<H>.parquet`, so a re-run
  overwrites rather than appends. **Hour files are cut by block arrival time, not trade
  `time`** (measured: a trade at 11:59:59.9 sits in `12.lz4`), so file `H` can hold
  events whose `dt`/hour is `H-1`; the writer keys `dt` from event `time` (NFR-5) and the
  object key's `hour=<H>` is the *source file* hour, never assumed to equal the event
  hour. Healing a gap must therefore fetch the gap's hours **plus the following hour**. Firehose objects carry their own generated names under
  `source=ws/`; bronze duplicates from the live path are expected and resolved downstream.
- **Table format:** silver/gold are Apache Iceberg tables in the **Glue catalog**, created
  and written exclusively through dbt-athena — one Iceberg write path, owned by dbt.
- **Transforms:** dbt-athena; medallion bronze → silver → gold. Dedup and the batch/stream
  seam are resolved at silver.
  - **Runtime:** a GitHub Actions `dbt-run` workflow over the OIDC role; no dbt container
    in AWS ([ADR-001](decisions/ADR-001-dbt-runtime-github-actions.md)).
  - **Iceberg maintenance:** `make iceberg-maintain` runs Athena `OPTIMIZE` + `VACUUM` on
    silver/gold as the last step of `session-down`.
- **Orchestration:** EventBridge schedules + Step Functions — scoped to the **backfill
  Lambda fan-out** (one invocation per archive hour file; a Map state over the requested
  hour list, also used by `make heal` for gap intervals). No Airflow/MWAA (cost). dbt is
  not orchestrated from AWS — see Transforms.
- **IaC:** Terraform, single root module, `apply`/`destroy` as the demo lifecycle. Optional
  persistent layer (S3 data + catalog) separated from the ephemeral layer (compute, streams)
  so data can survive teardown when desired.

### Data model (draft — freezes when OQ-1's `tid` parity check passes)

- **Bronze — `trades_raw`** (plain Parquet, partitioned `coin=<market, colon→underscore>/dt=<utc-date>`, where
  `dt` is derived from the **exchange event time**, not arrival time — see NFR-5): an
  **ingester-owned envelope** (resolves OQ-7) — best-effort typed columns (`tid`, `coin`,
  `side`, `px`, `sz`, `time`, etc.) plus a `raw_payload` JSON string column carrying the
  untouched source event, so the Firehose schema is owned by our ingester rather than by
  Hyperliquid. Source drift degrades to null typed fields instead of a lost record. Plus
  lineage columns `source` (`ws` | `backfill`), `ingested_at`, `session_id`. Backfill and
  stream write the identical envelope.
  - **Grain: exactly one row per `tid` per source.** The WS `trades` feed is one message
    per trade; **both candidate archives are fill-level** (measured 2026-09-04): one row
    per *user fill*, two rows per `tid` (maker + taker), with ~2 % single-fill `tid`s in
    the official archive. The backfill Lambda **collapses before writing bronze**: keep
    the taker-side (`crossed = true`) fill as the canonical row, or whichever single fill
    exists (`px`, `sz` are identical across a pair — nothing is summed), and record
    `archive_rows_collapsed` (1 or 2) in the envelope. The collapse rule lives in the
    backfill reader, so both paths emit the same grain and silver never sees it.
  - **Feed carries fewer fields than the archive:** the WS `trades` message is
    `coin, side, px, sz, time, hash, tid, users[2]` — no liquidation, crossed, or fee
    fields. Those typed columns are null for `source = ws` rows and populated only when
    the archive replay upserts them at silver. `users` stays inside `raw_payload` only.
  - **Retention:** bronze is never pruned for windows under reconciliation — it is the
    evidence for G3.
  - **Decimal precision:** `px decimal(18,8)`, `sz decimal(18,6)` — **confirmed** by the
    2026-09-04 spike (widest observed: px 5 int / 4 frac, sz 5 int / 5 frac across all
    `xyz:` markets and the core perps).
- **Silver — `trades`** (Iceberg): one row per trade, exactly-once. Typed columns: `tid`
  (trade id — the dedup key), `coin`, `side`, `px` (decimal), `sz` (decimal), `time` (UTC
  timestamp), liquidation/crossed flags if the source provides them, and
  `first_seen_source` (insert-only lineage). Built by dbt incremental merge on `tid`,
  bounded by a config-driven `dt` lookback (default 2 days); late or replayed data upserts
  cleanly.
  - **Source precedence in the merge:** the archive row is the richer record (flags, fee,
    counterparties), so **`backfill` always wins on update and `ws` never overwrites a
    row whose `first_seen_source` or last-writer is `backfill`**. Implemented as a
    `source_rank` (`backfill = 2`, `ws = 1`) in the merge predicate
    (`when matched and source.rank >= target.rank then update`), not via
    `merge_update_columns` alone. This is what lets a late feed duplicate arrive after the
    backfill without nulling the flags it filled.
- **Reconciliation — `recon_trades`** (dbt model over **bronze**): per **distinct** `tid`
  per source (bronze may hold duplicates from the live path), counts `ws_only` /
  `backfill_only` / `both` over the **reconcilable window** = session hours whose archive
  hour file has landed (trailing partial hour excluded). Tests: `ws_only = 0`; every
  `backfill_only` trade's `time` lies inside a gap interval from the session manifest.
  That pair of assertions *is* G3
  ([ADR-003](decisions/ADR-003-g3-reconciliation-at-bronze.md)). Output goes into the
  session manifest.
- **Gold** (Iceberg marts): `ohlcv_1m` / `ohlcv_1h` / `ohlcv_1d` per coin, `volume_daily`,
  and `liquidations_daily` — the last is **backfill-only** (FR-4): the feed carries no
  liquidation flag, so the mart is complete only for hours the archive has healed.

### Ingestion contract & failure modes

Contract: **at-least-once into bronze, exactly-once at silver** (dedup on `tid`).

| Failure | Behavior |
|---|---|
| WS disconnect mid-session | Reconnect with backoff; gap interval `[last_trade_time, first_trade_after_reconnect)` appended to the session manifest. Healing is explicit: `make heal` reads the manifest's unhealed gaps, runs the backfill Map over the covering hour files once they have landed (~1 h lag), then re-runs `dbt-run`; the manifest flips each gap to `healed: true` |
| Duplicate delivery (reconnect, Kinesis retry) | Expected; resolved at silver by the `tid` merge |
| Late / out-of-order events | Absorbed by the silver incremental merge; OHLCV rebuilds affected windows |
| Firehose delivery failure | Failed records land under an S3 `errors/` prefix; CloudWatch alarm active during sessions |
| Official archive hour file late or missing | Backfill for that hour is retried on the next run; if still absent after 24 h, fall back to the Reservoir daily file for that date through the column-mapping reader; both readers share the bronze layout |

## 6. Requirements

### Functional

- **FR-1** Ingest live trades for a configurable coin list from the HyperCore WebSocket API
  into bronze via Kinesis/Firehose.
- **FR-2** Backfill a configurable historical window from the S3 archive into the same
  bronze layout. Default window: **30 days** (measured cost ≈ $1 Lambda, ~33 GB read,
  free in-region transfer). Extending to the full archive history (from 2025-07-27) is a
  config change costing roughly $1 per additional month; do it only if a demo needs it.
- **FR-3** Silver layer: typed, schema-enforced, exactly-once-per-trade (dedup on trade id),
  with the batch/stream seam explicitly handled and tested.
- **FR-4** Gold layer: at minimum OHLCV candles and daily volume marts. A liquidation
  mart is **backfill-only by construction** (the feed carries no liquidation flag) and is
  documented as such rather than presented as real-time.
- **FR-5** All layers queryable in Athena; sample queries committed in the repo.
- **FR-6** One-command build (`terraform apply` + a documented make target) and teardown
  (`terraform destroy`) with a checklist proving nothing billable is left behind.
- **FR-7** Per-run cost captured (Cost Explorer, filtered on the `project=hyperlake` cost
  allocation tag) and committed to a `costs/` log in the repo. **Phase 0 must activate the
  cost allocation tag** — activation takes up to 24 h and Cost Explorer lags a further day,
  so the manifest records cost as `pending` at session end and a `make cost-backfill`
  target fills it in the next day.
- **FR-8** Session lifecycle is scripted and first-class: `make session-up` /
  `make session-down` bring the ephemeral layer up/down and write a **session manifest**
  (coins, start/end, gap intervals with `healed` flags, `recon_trades` counts, per-run
  cost) committed to the repo. `session-down` ends with `dbt-run` (via `gh workflow run`)
  and `iceberg-maintain`. A separate **`make heal`** target, run ≥ 1 h after session end,
  backfills the manifest's unhealed gap hours and the session's trailing hour, re-runs
  `dbt-run`, and updates the manifest — this is what makes G3's assertion hold for a
  session that had disconnects.
  - **Workflow completion contract:** `gh workflow run` is asynchronous, so every caller
    (`session-down`, `heal`) passes a unique `run_key` input, locates its run by that key
    (never "latest" — two runs can be seconds apart), then `gh run watch --exit-status`
    with a **20-minute timeout**. Failure or timeout exits non-zero and the manifest
    records the run URL with `status: failed`. One shared shell helper owns this.
  - **Two cost columns per session:** `cost_estimate` is computed at `session-down` from
    resource-hours × list price (Fargate, Kinesis, Firehose GB, Athena bytes) and is
    available immediately; `cost_actual` starts `pending` and is filled by
    `make cost-backfill` the next day. The demo shows the committed `costs/` table with
    prior sessions' estimates beside actuals — a live number is not possible (Cost
    Explorer lags ~24 h) and the estimate-vs-actual history is the stronger claim.
  - **Session reaper (dead-man's switch):** `session-up` creates a **one-time EventBridge
    Scheduler entry at start + `max_session_hours` (default 6)** that invokes a small
    `session-reaper` Lambda: scale the ingester service to 0, **wait one full Firehose
    buffer window plus margin (~2 min) so Firehose drains what is still in the stream**,
    then delete the Kinesis stream and write `reaped: true` to the manifest. Deleting
    immediately would lose the last 1–2 min of trades Firehose has not yet read.
    `session-down` deletes the schedule. The ingester also self-exits at the same limit
    (`--max-session-hours`) as a no-infra fallback. Bounds a forgotten session to ~6 h
    (~$0.40) instead of ~$1.55/day until the budget alarm fires.
    *Terraform drift:* the reaper deletes a resource Terraform owns in the ephemeral
    layer. The next `session-up` runs `terraform apply` with refresh, which sees the
    stream missing and recreates it; a `session-down` after a reap runs `destroy`, which
    must tolerate the already-deleted stream. **Verify both paths in Phase 2** on a
    deliberately reaped session before the reaper is trusted.
- **FR-9** Local development works without AWS via a **two-target dbt project**
  (`duckdb` / `athena`, materialization switched by macro —
  [ADR-002](decisions/ADR-002-two-target-dbt-project.md)). CI (GitHub Actions) runs
  pytest + `dbt build --target duckdb` on committed sample fixtures + `terraform
  fmt/validate` on every PR — free, no cloud credentials. CI proves logic, uniqueness, and
  OHLCV math. **Merge behaviour has its own automated test on Athena:** a `seam_test`
  schema seeded by dbt with a few dozen fixture rows, and `dbt build --select tag:seam`
  runs the silver merge twice covering (a) a late feed duplicate, (b) backfill arriving
  after the feed row, (c) a feed row arriving after backfill — asserting
  `first_seen_source`, flag preservation, and row counts. It runs in the `dbt-run`
  workflow **on every push to `main`** (needs the OIDC role, so not on PRs) and costs
  cents. Ingester unit-tested against recorded WS fixtures.

### Non-functional

- **NFR-1 Cost:** hard budget $15/month during active development; AWS Budgets alarm at $10
  provisioned by Terraform on day one. The alarm is a **lagging backstop** (Budgets data
  refreshes on a multi-hour delay, typically 8–12 h) — the primary guardrails are the
  scripted `session-down` (FR-8), the **session reaper** that tears down a session left
  running past `max_session_hours` (FR-8), and the post-destroy checklist (FR-6).
- **NFR-2 No bill-surprise services:** NAT Gateway, MWAA, MSK provisioned, OpenSearch, and
  QuickSight are banned. Fargate runs in the **default VPC** with a public IP and an
  egress-only security group; no VPC, subnet, NAT, or endpoint resources of our own.
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
| **0 — Scaffold** | Repo, Terraform bootstrap (state, budget alarm, OIDC, **cost allocation tag activated**), CI skeleton (two-target dbt project compiles), `costs/` log with the `cost_estimate` / `cost_actual` schema and `make cost-backfill` | Cost guardrails exist before any resource does |
| **1 — Lakehouse (batch)** | Close OQ-1 (`tid` parity check, ~1 evening — sizing/grain/region already measured in the [2026-09-04 spike](spikes/2026-09-04-oq1-archive-desk-spike.md)) → Lambda backfill (per hour file) → bronze Parquet → silver Iceberg via dbt (`dbt-run` workflow) → Athena queries | Lakehouse fundamentals, requester-pays handling |
| **2 — Streaming** | Fargate ingester → Kinesis → Firehose → same bronze; `session-up`/`session-down` with manifest; **session reaper** (Scheduler + Lambda) and ingester self-exit | Streaming ingestion, live demo capability, bounded blast radius |
| **3 — Convergence + transforms** | dbt silver/gold, dedup at the seam, data quality tests | The actual hard problem; the interview talking point |
| **4 — Presentation** | README + diagram, **demo runbook** (`docs/demo-runbook.md`: session-up → stream → heal → recon → query, with timings), recorded demo following the runbook, cost report, dbt docs | Legibility to the hiring audience |

Phases ship sequentially; each ends with a working, demoable state and a teardown test.

## 8. Cost model (approximate, **ap-northeast-1** list prices — both archive buckets live there; ~10–15 % above us-east-1)

Volume basis (measured, [spike 2026-09-04](spikes/2026-09-04-oq1-archive-desk-spike.md)):
watchlist ≈ **1.1 M trades/day ≈ 17 trades/s**, ~300 B/trade raw → ~300 MB/day JSON,
~60–80 MB/day as Parquet. A 4 h session streams ~185 k trades ≈ 55 MB raw.

| Item | Basis | Per ~4 h demo |
|------|-------|---------------|
| Fargate ingester (0.25 vCPU / 0.5 GB) | ~$0.016/hr | ~$0.07 |
| Kinesis on-demand | ~$0.048/hr + ~$0.10/GB | ~$0.20 |
| Firehose (ingest + Parquet conversion) | ~$0.036/GB + $0.022/GB | pennies |
| Firehose dynamic partitioning | ~$0.024/GB + $0.006/1k objects | pennies |
| Athena (queries + OPTIMIZE/VACUUM) | $5/TB scanned; silver ≈ 1.5 GB for 30 days | pennies |
| dbt runs (incl. seam test on push to `main`) | GitHub Actions minutes + a few KB Athena scan | $0 |
| Session reaper (EventBridge Scheduler + Lambda) | 1 schedule + ≤ 1 invocation per session | $0 |
| **Session total** | | **≈ $0.50–1** (budget stays $2) |

One-time 30-day backfill, deployed in ap-northeast-1 (S3 → Lambda transfer is free in-region):

| Source | Bytes read | Lambda compute | Requester-pays requests |
|---|---|---|---|
| Official (primary) | ~33 GB LZ4, 720 hour files | ~58 k GB-s ≈ **$1** | negligible |
| Reservoir (fallback) | ~22 GB Parquet, 60 daily files | ~7 k GB-s ≈ **$0.15** | negligible |

Idle: S3 storage only — bronze + silver + gold for 30 days ≈ **< 5 GB ≈ $0.15/month**
(prior 20–50 GB estimate was before measurement). No free-tier credits assumed (account's
credits are exhausted) — the budget alarm (NFR-1) is the backstop.

## 9. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Teardown misses a billable resource | Silent monthly burn | Everything in one Terraform state; budget alarm; post-destroy checklist (FR-6) |
| WS feed schema drift / undocumented changes | Broken ingester mid-demo | Ingester-owned envelope (OQ-7): drift degrades to null typed fields, `raw_payload` retains the source event, nothing dies at Firehose conversion; schema enforcement proper happens at silver |
| Archive format differs from WS format | Convergence complexity explodes | Measured 2026-09-04: official archive fields are WS-identical; only the fill→trade collapse differs and it is isolated in the backfill reader |
| Reservoir (fallback) layout drifts — already reorganised once (`_pre_hip4_unification_backup/`) | Fallback reader breaks silently | Fallback reader pinned to a path pattern + schema assertion; failure is loud, not null-filled |
| Kinesis/Fargate left running after a session | ~$30+/month | Session start/stop is a scripted pair; destroy is part of the demo script, not an afterthought |
| Free-tier credit assumptions wrong | Unplanned spend | Budget alarm at $10 is independent of credits |
| Scope creep toward a "product" (dashboards, alerts, 24/7) | Never ships | Non-goals section; PRD freeze after OQs resolve |
| AWS-beginner missteps (IAM, VPC, billing) | Slow phases, surprise config | Per-phase learning spikes; no-VPC-by-default (public-subnet Fargate only); budget alarm from day 0; everything in Terraform so mistakes are reviewable and reversible |
| Publishing raw data from a public repo | ToS / redistribution questions | Commit only small derived samples (gold marts) as fixtures; link to sources for raw data |

## 10. Settled decisions

One line each; the *why* lives in the linked ADR, or in the ADR that opens the decision's
phase (NFR-6). Pre-ADR decisions were firmed in the 2026-07-09 planning discussion unless
dated otherwise.

| Decision | Choice | ADR |
|---|---|---|
| Name | `hyperlake` (collision-checked; no affiliation claim on "Hyperliquid") | — |
| Platform | AWS serverless, ephemeral apply/destroy — over VPS for hiring signal + near-zero idle | Phase 0 |
| AWS account | owner's existing account; dedicated IAM role + `project=hyperlake` tags; no free-tier credits assumed | Phase 0 |
| Tracker | Linear (Quant team); process private, repo public | — |
| Watchlist (OQ-3) | BTC, ETH, HYPE + `xyz:SP500`, `xyz:XYZ100` (HIP-3); config-driven; **measured ~1.1 M trades/day, ~17/s** (2026-09-04) | Phase 1 |
| Region (2026-09-04, from OQ-1 spike) | **ap-northeast-1** — both archive buckets live there; in-region S3→Lambda transfer is free | Phase 0 |
| Backfill source (provisional, 2026-09-04) | official `hl-mainnet-node-data` primary (~1 h lag, WS-identical fields); Reservoir fallback via column mapping — final on `tid` parity pass | OQ-1 ADR |
| Ingester language | Python | — |
| License (OQ-5, 2026-07-10) | MIT | — |
| Catalog (OQ-2, 2026-07-10) | Glue catalog; S3 Tables parked — beginner fluency + dbt-athena maturity | Phase 1 |
| Write path (2026-07-10) | bronze = plain Parquet, Hive partitions; Iceberg only at silver/gold, written only by dbt-athena; backfill = plain-Python Lambda | Phase 1 |
| Bronze drift posture (OQ-7, 2026-09-01) | ingester-owned envelope: typed columns + `raw_payload`; drift → nulls, never lost records | Phase 2 |
| Demo artifact (OQ-4, 2026-09-01) | recorded video primary; scripted live run on request | Phase 4 |
| Timeline (OQ-6, 2026-09-01) | no deadline; ~1–2 weekends per phase; Phase 3 uncompressed | — |
| dbt runtime (2026-09-04) | GitHub Actions `dbt-run` workflow over OIDC; Step Functions = backfill fan-out only | [ADR-001](decisions/ADR-001-dbt-runtime-github-actions.md) |
| Local dbt parity (2026-09-04) | two targets `duckdb`/`athena`; CI proves logic, Athena proves merge | [ADR-002](decisions/ADR-002-two-target-dbt-project.md) |
| G3 proof location (2026-09-04) | reconciliation over bronze (`recon_trades`); silver keeps `first_seen_source` for lineage only | [ADR-003](decisions/ADR-003-g3-reconciliation-at-bronze.md) |
| Merge seam test (2026-09-04) | `seam_test` schema + `tag:seam` dbt build on Athena, on every push to `main`; three ordering cases | [ADR-002](decisions/ADR-002-two-target-dbt-project.md) (amended) |
| Workflow completion (2026-09-04) | `run_key` input + `gh run watch --exit-status`, 20 min timeout, shared helper; failures are loud and land in the manifest | Phase 1 |
| Session cost reporting (2026-09-04) | `cost_estimate` at session-down + `cost_actual` next day; demo shows the estimate-vs-actual history, never a "live" number | Phase 0 |
| Session reaper (2026-09-04) | **built, not parked**: one-time EventBridge Scheduler at start + 6 h → reaper Lambda (scale to 0, delete stream); ingester self-exit as fallback | Phase 2 |

## 11. Open questions

Resolves to an ADR before its dependent phase starts.

- **OQ-1 — Backfill source (region + volume + grain now measured):** desk spike on
  2026-09-04 ([spike note](spikes/2026-09-04-oq1-archive-desk-spike.md)) settled most
  criteria without AWS infra:

  | Criterion | Official `hl-mainnet-node-data` | Reservoir `hydromancer-reservoir` |
  |---|---|---|
  | Region | ap-northeast-1 | ap-northeast-1 → **region decided either way** |
  | Freshness lag | ~1 h → G3 replay fits inside one demo session | 10–34 h → G3 needs a pre-chosen historical window |
  | Schema fit vs WS | field names identical; envelope is pass-through | renamed/typed (`trade_id`, `price`, `size`, `timestamp`) → mapping layer |
  | HIP-3 coverage | `xyz:SP500`, `xyz:XYZ100` present | present (xyz from 2025-10-13) |
  | Grain | fill-level, 2 rows/`tid`, ~2 % singles → collapse rule required | fill-level, exactly 2 rows/`trade_id` → collapse rule required |
  | 30-day backfill cost | ~$1 Lambda, 33 GB read | ~$0.15 Lambda, 22 GB read |
  | Format effort | stream-decode LZ4 + JSON lines | Parquet, trivial |
  | Provenance / drift | first-party | third-party; layout reorganised recently (`_pre_hip4_unification_backup/`) |

  **Provisional decision:** official bucket **primary**, Reservoir **fallback** behind a
  column-mapping layer. Freshness is the deciding factor — it makes the G3 demo
  self-contained. Volume for FR-2's 30-day window is confirmed affordable (< $2).

  **Remaining hard gate — `tid` parity (pass/fail):** capture ≥ 1 min of WS trades at
  hour `H`, wait for `hourly/YYYYMMDD/H.lz4` (~`H+2:05` UTC), assert every captured `tid`
  appears with identical `coin, px, sz, side, time`. Both sides carry `tid` and `hash` of
  the same shape, so failure is unlikely but not yet excluded. If it fails, **plan B is
  `hash`** (the L1 transaction hash, present on both the feed and both archives) combined
  with `coin, side, px, sz` — far less collision-prone than a time-based composite key,
  though one order filling against several resting orders shares a `hash`, so it must be
  validated the same way. Only if both fail does G3 get re-scoped **before** the bronze
  schema freezes. Passing closes OQ-1 with an ADR. *(Blocks Phase 1 build, not the Phase 0 scaffold; ~1 evening.)*

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
