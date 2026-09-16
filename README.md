# hyperlake

[![ci](https://github.com/noahwins-ng/hyperlake/actions/workflows/ci.yml/badge.svg)](https://github.com/noahwins-ng/hyperlake/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)](pyproject.toml)
[![Terraform](https://img.shields.io/badge/terraform-%3E%3D1.9-7B42BC.svg)](infra)

Streaming lakehouse for Hyperliquid market data: live WebSocket trades and S3 archive
backfill converging into the same Iceberg tables on AWS serverless, reproducible from zero
with one `terraform apply` and torn down after every session.

## Why this exists

Most portfolio data pipelines run on a Kaggle CSV, and most that run in the cloud bleed money
24/7 until they rot. Hyperlake avoids both. It ingests a real, high-volume feed (Hyperliquid
trades, ~6.6 M/day network-wide, ~1.1 M/day on the default watchlist) through two genuinely
different paths: a live WebSocket stream and a requester-pays S3 archive. Landing both in one
table with exactly-once semantics is the batch/stream convergence problem that makes lakehouse
design interesting, and proving it (not asserting it) is the project's central claim.

Nothing runs 24/7: a session is `terraform apply` → work → `destroy`, bounded by a
dead-man's-switch reaper if nobody runs the teardown. Sessions cost about $0.30; idle is near
zero. Market-data engineering only, with no trading, signals, or execution anywhere.

**Data scope.** The `trades` WebSocket channel only (no order book, no candles), for the five
markets in [`config/watchlist.yaml`](config/watchlist.yaml): BTC, ETH, HYPE, and the HIP-3
markets `xyz:SP500` and `xyz:XYZ100`, about 17 trades/s in total. Backfill reads the official
`hl-mainnet-node-data` hourly archive. Widening to more of Hyperliquid's ~440 markets is a
config change, not a code change.

## Architecture

Both paths write the same ingester-owned envelope into one bronze table. dbt, run from
GitHub Actions over OIDC rather than from AWS, is the only writer for silver and gold. Step
Functions orchestrates the backfill fan-out only.

```mermaid
flowchart LR
    subgraph batch["Batch: archive backfill"]
        ARC[("hl-mainnet-node-data<br/>archive hour files")] --> SFN["Step Functions Map<br/>(make backfill / make heal)"]
        SFN --> LAM["Backfill Lambda<br/>(official reader + Reservoir fallback)"]
    end

    subgraph live["Streaming: session-scoped"]
        WS(["Hyperliquid WS trades"]) --> FARGATE["Fargate ingester"]
        FARGATE --> KIN["Kinesis"]
        KIN --> FH["Firehose"]
    end

    LAM --> BRONZE[("bronze.trades_raw<br/>Parquet, coin=/dt=/source=")]
    FH --> BRONZE

    BRONZE --> DBT["GitHub Actions dbt-run.yml<br/>(OIDC, dbt-athena)"]
    DBT --> SILVER[("silver.trades<br/>Iceberg, merge on tid")]
    DBT --> RECON[("recon_trades<br/>convergence check")]
    SILVER --> GOLD[("gold marts<br/>ohlcv_1m/1h/1d, volume_daily,<br/>liquidations_daily")]

    BRONZE --> ATHENA{{"Athena (cloud) /<br/>DuckDB (local + CI)"}}
    SILVER --> ATHENA
    GOLD --> ATHENA
```

Component-by-component detail of what is deployed today is in
[`docs/architecture/system-overview.md`](docs/architecture/system-overview.md).

## Proof it works

Every number below is a real measurement recorded in the repo, cited by date and session.

- **8m 53s from `terraform apply` to a queryable Athena result.** A real 1-day backfill of
  867,681 trades, measured 2026-09-11 against the 15-minute target
  ([ops runbook](docs/guides/ops-runbook.md#g1-stranger-path-timing--bootstrap--backfill--athena-query-qnt-467-ac1)).
- **Convergence, proven by reconciliation.** A live session streamed one full clock hour,
  the same hour was backfilled from the archive, and the bronze-level set comparison came
  back `ws_only = 0`, `both = 132,823`, `backfill_only = 13,577`, all but one inside the
  session's recorded WebSocket-disconnect gap; the one residual (534 ms past the gap end) is
  documented, not hidden (2026-09-11, session `qnt-466-20260911124320`;
  [spike report](docs/spikes/2026-09-11-qnt466-g3-live-replay.md)).
- **$0.30 average session cost** over 12 finalized sessions, against a $2 ceiling. See
  [Cost](#cost).
- **dbt docs regenerated on every push to `main`**, offline on DuckDB; every silver/gold/recon
  column is described, enforced by
  [a pytest over `manifest.json`](tests/test_dbt_docs_columns_described.py). Lineage as of
  2026-09-16:

![dbt docs lineage graph: bronze.trades_raw feeding silver trades, which feeds the gold OHLCV/volume/liquidations marts and their tests, and bronze.trades_raw feeding recon_trades](docs/img/dbt-lineage.png)

The same coin and day traced through all three layers, from the demo session run 2026-09-12
(`qnt-468-20260912134330`, [demo runbook](docs/demo-runbook.md)):

| Layer | Query | Result |
|---|---|---|
| bronze | `make bronze-query DT_FROM=2026-09-12` | 2,197 `source='ws'` rows in 2.0 s (at-least-once: duplicates allowed) |
| silver | `select count(*), count(distinct tid) from silver.trades where …` | `row_count=2157, distinct_tid=2157` in 4.7 s (the 40 bronze duplicates resolved by the merge) |
| gold | `select * from gold.ohlcv_1m where coin='BTC' …` | `2026-09-12 13:44:00  open=77264.0  close=77261.0  volume=4.40628` in 3.2 s |

Full per-layer query sets: [`bronze.sql`](docs/queries/bronze.sql) ·
[`silver.sql`](docs/queries/silver.sql) · [`gold.sql`](docs/queries/gold.sql).

## Design decisions

One line each; the reasoning lives in the linked ADR.

- **dbt runs from GitHub Actions, not from AWS.** No always-on runtime, no container to
  host; Step Functions only fans out the backfill.
  [ADR-001](docs/decisions/ADR-001-dbt-runtime-github-actions.md).
- **Two dbt targets, one macro owning the seam.** DuckDB proves the logic offline in CI;
  the Iceberg merge is proven on Athena by a seam test on every push to `main`.
  [ADR-002](docs/decisions/ADR-002-two-target-dbt-project.md).
- **Convergence is proven at bronze, not silver.** Silver's merge keeps one row per trade,
  so only the append-only raw layer can still show which sources saw it.
  [ADR-003](docs/decisions/ADR-003-g3-reconciliation-at-bronze.md).
- **Kinesis + Firehose over a direct Fargate → S3 write.** Firehose owns buffering, Parquet
  conversion, and partitioning; the ingester stays a thin WebSocket client.
  [ADR-004](docs/decisions/ADR-004-kinesis-firehose-over-direct-write.md).
- **Official node archive as backfill source, `tid` as trade identity.** The identity gate
  passed 1,986/1,986 between feed and archive before the schema was frozen.
  [ADR-005](docs/decisions/ADR-005-backfill-source-and-trade-identity.md).
- **Guardrails.** One Iceberg writer (bronze is plain Parquet; only dbt-athena writes
  Iceberg). Event time everywhere, never arrival time. No NAT Gateway, MWAA, MSK, OpenSearch,
  or QuickSight; Fargate runs with a public IP and an egress-only security group. No
  long-lived AWS keys; GitHub Actions assumes a role over OIDC, CI runs with none.

## Stack

| Layer | Service | Why |
|---|---|---|
| Live ingest | ECS Fargate (Python, `websockets`) | Only compute that exists during a session; no VPC plumbing of its own |
| Streaming | Kinesis Data Streams (on-demand) → Firehose | Managed buffering + Parquet conversion; ~17 trades/s never needs a shard plan |
| Batch backfill | Lambda + Step Functions Map + EventBridge | Plain-Python per archive hour; no Spark cluster to size or pay for |
| Storage | S3: Parquet (bronze), Iceberg (silver/gold) | Hive partitions where append-only is enough; Iceberg where merge semantics are needed |
| Catalog + query | Glue Data Catalog, Athena (cloud) · DuckDB (local, CI) | Serverless per-query billing; same dbt models run offline |
| Transform + tests | dbt (`dbt-athena`, `dbt-duckdb`) | Contract, freshness, reconciliation, and OHLCV-invariant tests gate gold |
| Infrastructure | Terraform (bootstrap · persistent · ephemeral roots) | Everything tagged `project=hyperlake`; nothing hand-created |
| CI/CD | GitHub Actions, OIDC role | Offline CI on every PR; `dbt-run` and the seam test over OIDC |
| Cost guard | EventBridge Scheduler reaper, AWS Budgets | Forgotten sessions die at 6 h; Budgets is only the lagging backstop |

## Repo layout

| Path | What lives there |
|---|---|
| `src/hyperlake/` | Envelope, watchlist loader, partition helper, WebSocket ingester, backfill readers, session lifecycle, reaper, heal logic |
| `dbt/` | Models (`silver`, `gold`, `recon`, `seam`), macros, contract tests, fixtures |
| `infra/` | Terraform: `bootstrap/` (state, OIDC, budgets), `main/persistent/` (S3, Glue, Athena), `main/ephemeral/` (compute, streams) |
| `scripts/` | `session_up`/`session_down`, `heal`, `recon`, `backfill`, `bronze_query`, cost tooling, doc checks |
| `config/watchlist.yaml` | The market list; the only place markets are named |
| `sessions/` · `costs/` | One committed manifest per demo session; `sessions.csv` with estimate vs. next-day actual |
| `docs/` · `tests/` | PRD, ADRs, architecture overview, runbooks, spikes, retros; the pytest suite |
| `.github/workflows/` | `ci.yml`, `dbt-run.yml`, `ingester-image.yml`, `tf-drift-check.yml`, `verify-oidc.yml` |

## Run it yourself

**Prerequisites.** An AWS account with an admin-ish identity for the one-time bootstrap,
Terraform ≥ 1.9, AWS CLI v2, `uv`, and `gh`. Region is `ap-northeast-1` (both archive
buckets live there). Full walkthrough: [`docs/guides/bootstrap.md`](docs/guides/bootstrap.md).

**Run.** Bootstrap once per account, then apply and backfill one day:

```
cd infra/bootstrap && terraform init && terraform apply   # one-time per AWS account
# wire infra/main/backend.hcl from the bootstrap outputs (see the bootstrap guide)
make tf-apply-persistent
make tf-apply-ephemeral
make backfill FROM=<day> TO=<day>                         # e.g. 2026-09-10
make dbt-run ARGS="-f vars='{\"freshness_window_start\": \"<day> 00:00:00\", \"freshness_window_end\": \"<day+1> 01:00:00\"}'"
```

**Verify.** `make bronze-query DT_FROM=<day>` for the day's bronze rows, then the
exactly-once check from [`silver.sql`](docs/queries/silver.sql): row count equals distinct
`tid` count. For the streaming path (`session-up` → stream → `heal` → `recon` → query)
follow [`docs/demo-runbook.md`](docs/demo-runbook.md).

**Tear down.** The ephemeral stack is the only thing that costs money while idle:

```
make tf-destroy-ephemeral          # after a backfill; `make session-down` does this for sessions
make tf-destroy-persistent         # optional: also removes the data bucket, catalog, and workgroup
```

A one-day backfill costs on the order of $0.10; the persistent layer idles below $1/month.

## Cost

Every session's estimate is written at teardown and its actual is backfilled from Cost
Explorer the next day; both live in [`costs/sessions.csv`](costs/sessions.csv). The table
below is generated by `make cost-report`; per-session detail and the Cost Explorer
reconciliation are in [`docs/costs.md`](docs/costs.md).
<!-- COST_REPORT:START -->
| | |
|---|---|
| Average session cost | $0.30 |
| Highest session cost | $0.76 |
| Idle cost (highest month, Cost Explorer) | $-0.92/month |
| Target ceiling | < $2/session, < $2/month idle |
| Cost Explorer reconciliation gap | $-0.92 (full detail: [docs/costs.md](docs/costs.md)) |
<!-- COST_REPORT:END -->

**What this would cost you:**

| Scenario | Cost | Source |
|---|---|---|
| Idle, nothing running | under $1/month (transiently negative right after a session while Cost Explorer catches up) | [`docs/costs.md`](docs/costs.md) |
| One demo session | $0.13–$0.76, avg $0.30 across 12 sessions | [`costs/sessions.csv`](costs/sessions.csv) |
| One-day backfill | under $0.01 of Lambda compute | [ops runbook](docs/guides/ops-runbook.md#backfill-step-functions-fan-out--measured-wall-time--cost-qnt-452) |
| 24×7 streaming, 30 days *(model, never run)* | $50–$100/month | [`costs/README.md`](costs/README.md#what-247-would-cost) |

In the 24×7 model, Kinesis's on-demand hourly charge is ~72% of metered spend and accrues
whether or not a trade arrives, which is exactly why the stream is torn down between sessions.

## Testing and CI

- **Offline CI on every PR** (`make check` mirrors it exactly): ruff, pyright, pytest,
  pip-audit, `dbt build --target duckdb` (75 models and tests), `terraform fmt`/`validate`,
  and a grep that fails on any long-lived AWS key. Zero cloud credentials.
- **Athena seam test on every push to `main`**: three merge-ordering cases run against a real
  Iceberg table over OIDC, because DuckDB cannot prove `MERGE` semantics.
- **dbt contract tests gate gold**: schema, freshness, and volume tests on silver must pass
  before any gold mart builds; convergence tests run over bronze; OHLCV invariants on gold.
  `make dbt-demo-fail` shows a deliberate failure and the downstream skips.
- **Daily Terraform drift check** flags anything in the Glue catalog that Terraform did not create.
- **Docs checks**: `make docs-check` fails on any broken relative link;
  `make demo-runbook-check` fails if the runbook names a `make` target that does not exist.

## What I would do differently

- **Bronze partition projection was a cost trap.** ~4,000 virtual partitions and no persisted
  metadata means one unbounded query triggers an S3 LIST per partition; that was most of the
  early spend. A bounded query wrapper fixed the symptom; persisted metadata is the real fix.
- **Kinesis + Firehose is more machinery than ~17 trades/s needs.** Chosen for managed
  Parquet conversion and learning value; a direct Parquet write would be simpler at this volume.
- **Local merge parity is a real gap.** The seam test runs only on `main`, so a bad merge
  change is caught after the PR. A Trino container in CI would close it.
- **Inducing a WebSocket disconnect took three attempts.** A gap-injection flag in the
  ingester would have replaced an afternoon of NACL edits.
- **Short demo sessions cannot reconcile.** The archive lands ~1 h after a clock hour closes,
  so the demo should have been designed around a session that spans one from the start.

## Further reading

- [PRD](docs/prd.md): scope, goals, cost model, and the frozen decisions
- [System overview](docs/architecture/system-overview.md): how it works now, component by component
- [Demo runbook](docs/demo-runbook.md): the streaming path with real timings and a data-quality failure demo
- [ADR index](docs/INDEX.md#decisions-adrs): every significant decision and its reasoning

## License

[MIT](LICENSE). Hyperlake is an independent portfolio project and is not affiliated with,
endorsed by, or connected to Hyperliquid or Hyperliquid Labs.
