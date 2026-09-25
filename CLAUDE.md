# Working Approach

## 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.** State assumptions explicitly; if
uncertain, ask. If multiple interpretations exist, present them. If a simpler approach exists, say
so. If something is unclear, stop and name it.

## 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.** No features beyond what was asked, no
abstractions for single-use code, no configurability that wasn't requested, no error handling for
impossible scenarios. If you wrote 200 lines and it could be 50, rewrite it.

## 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.** Don't "improve" adjacent code or
refactor what isn't broken. Match existing style. Remove orphans your change created; leave
pre-existing dead code (mention it, don't delete it). Every changed line should trace to the request.

## 4. Goal-Driven Execution
**Define success criteria. Loop until verified.** Turn tasks into verifiable goals ("add validation"
→ "write tests for invalid inputs, then make them pass"). For multi-step work, state a brief plan
with a verify step each.

## 5. Concise Output
**Answer what was asked and stop.** No preamble, no recap of what you just did, no closing summary;
a second answer to the same question is shorter than the first. Tickets: ≤5 lines of context, then
one-line ACs (condition + how it's proven), linking `docs/` or the ADR rather than restating it.

## 6. Fold Follow-Ups Into an Existing Ticket
**Feedback on work in flight belongs on a ticket that already exists.** A review comment or a defect
found while verifying, if it's inside the current ticket's scope, gets fixed on the same branch and
recorded on that ticket. If it belongs elsewhere but an open ticket covers that domain, add it there
as an AC. File a *new* ticket only when nothing covers it or it needs an unmade decision (ADR /
scope change), never to close the current one green.
See the flow package's `method/guidelines/scoping-and-tickets.md`.

---

# Hyperlake: Project Conventions

> **Status: Phases 0-4 shipped** (QNT-444..478); remaining work is reactive, on the perpetual
> Ops & Reliability milestone. Regenerated 2026-09-24 with `flow-gen-claudemd`.

## Core Philosophy

Portfolio project: a streaming lakehouse for Hyperliquid market data that a hiring manager can absorb
in ten minutes and a stranger can reproduce from the README. These rules are reviewed against, not
aspirational (PRD §3, §5; `workflow-profile.yaml` `architecture_rules`):

- **No trading, signals, or execution anywhere.** Market data engineering only, no order placement,
  wallets, or keys with financial power.
- **Ephemeral by design.** No 24/7 operation. Demo sessions are `terraform apply` → work → `destroy`;
  targets are < $2 per session and < $2/month idle. The scripted `session-down`, the session reaper,
  and the post-destroy audit are the guardrails; the AWS Budgets alarm is only a lagging backstop.
- **One Iceberg writer.** Bronze is plain Parquet with Hive partitions. Iceberg exists only at
  silver/gold and is written exclusively by dbt-athena. No other Iceberg writer may appear.
- **Backfill is plain-Python Lambda.** No Glue Spark ETL. Banned services (cost): NAT Gateway, MWAA,
  MSK provisioned, OpenSearch, QuickSight. Fargate runs in the default VPC with a public IP and an
  egress-only security group, no VPC plumbing of our own.
- **Ingestion contract:** at-least-once into bronze, exactly-once at silver (merge on `tid`; the
  archive row outranks the feed row on update). Bronze is never pruned for windows under
  reconciliation, it is the evidence for G3 (ADR-003).
- **Event time everywhere.** All timestamps UTC in storage. Partition `dt=` and every gold window
  derive from exchange event `time`, never from arrival/`ingested_at`.
- **Everything is Terraform, tagged `project=hyperlake`.** Nothing hand-created. No long-lived AWS
  keys in the repo or CI, GitHub Actions uses OIDC.
- **Watchlist is config-driven** (`config/watchlist.yaml`), no hardcoded market lists in code.
  HIP-3 markets keep their exact name in the `coin` column (`xyz:SP500`); the partition value is
  normalised `:` → `_` by one shared helper.


## Architecture

As built (PRD §5; component detail in `docs/architecture/system-overview.md`):

```
LIVE  (demo sessions only)
  Hyperliquid WS `trades` ─► Fargate ingester (Python, envelope) ─► Kinesis (on-demand, key=coin)
                                                                       └─► Firehose 60 s / 64 MB → Parquet
BATCH (per archive hour file, requester-pays, ap-northeast-1)                    │
  hl-mainnet-node-data hourly/YYYYMMDD/H.lz4 ─► EventBridge ► Step Functions Map ► Lambda
      stream LZ4 → filter watchlist → collapse fill pair → deterministic object key      │
                                                                                          ▼
                                            BRONZE  trades_raw   plain Parquet, coin=/dt=/source=
                                            (Glue partition projection; append-only; never pruned)
                                                                                          │
  GitHub Actions `dbt-run` (OIDC) → dbt-athena ──────────────────────────────────────────┘
      bronze → SILVER trades (Iceberg, merge on tid, first_seen_source)
      silver → GOLD ohlcv_1m/1h/1d · volume_daily · liquidations_daily (backfill-only)
      bronze → recon_trades  ws_only / backfill_only / both  = the G3 proof
                                                            Athena (cloud) · DuckDB (local + CI)
```

- Both paths emit the **same ingester-owned envelope** (typed columns + `raw_payload`), so upstream
  drift degrades to nulls, never lost records.
- dbt runs from GitHub Actions, not from AWS (ADR-001). Step Functions orchestrates the backfill
  fan-out only.
- Hour files are cut by block **arrival** time; a reader mapping trades to hours fetches `H` and
  `H+1`, and keys `dt` from event `time`.


## Stack

- **Python 3.12**, managed with uv (`pyproject.toml`, `uv.lock`); runtime deps `websockets`,
  `boto3`, `lz4`, `pyarrow`, `pyyaml`; dev group ruff, pyright, pytest, pip-audit; `dbt` group
  `dbt-athena-community` + `dbt-duckdb` 1.11.
- **Terraform** >= 1.9, three roots: `infra/bootstrap/` (local state: state bucket, OIDC role,
  budgets, cost tag), `infra/main/persistent/` (data bucket, Glue, Athena, ECR) and
  `infra/main/ephemeral/` (Fargate, Kinesis/Firehose, backfill Lambda + Step Functions, reaper),
  both on the S3 backend.
- **AWS ap-northeast-1**: Fargate, Kinesis Data Streams, Firehose, S3, Glue catalog, Athena,
  Lambda, Step Functions, EventBridge Scheduler, Budgets.
- **dbt** with two targets, `duckdb` (local + CI, plain tables) and `athena` (Iceberg incremental
  merge). One macro owns the difference (ADR-002). Merge behaviour is proven by an Athena seam test
  on every push to `main`, not on PRs.
- **GitHub Actions**: `ci.yml` (offline gate, the required `checks` status), `dbt-run.yml`
  (OIDC; build + seam jobs), `ingester-image.yml`, `tf-drift-check.yml` (daily),
  `verify-oidc.yml`.

## Repo Structure

```
src/hyperlake/        envelope, partitions, watchlist, ingester, session, session_reaper, heal, recon
  backfill/           official (node archive), reservoir (fallback), hour_list, state_machine
config/watchlist.yaml the only place markets are named
dbt/                  models/{staging,silver,gold,recon,seam}, macros, seeds, tests, fixtures (gold-safe)
infra/                bootstrap/ · main/persistent/ · main/ephemeral/
scripts/              session_up/down, heal, recon, backfill, bronze_query, cost_backfill/report,
                      tf_drift_check, audit_teardown, iceberg_maintain, gh_run.sh, doc checks
scripts/spike/        OQ-1 tid-parity gate and Kinesis spike helpers
tests/                pytest suite (+ fixtures/)
sessions/             one committed manifest JSON per demo session
costs/                sessions.csv (estimate vs Cost Explorer actual) + README
docs/                 prd, project-requirement, project-plan, architecture/, decisions/ (ADR-001..005),
                      spikes/, guides/, retros/, queries/, demo-runbook, costs.md (generated)
.github/workflows/    ci, dbt-run, ingester-image, tf-drift-check, verify-oidc
.githooks/            commit-msg enforces the commit convention; pre-push refuses direct pushes to main
workflow-profile.yaml the flow suite's project profile, the only per-project config
```

## Code Style

- Lint `make lint`, format `make format`, types `make types` (ruff, line length 100, rules
  E/F/I/UP; pyright basic over `src`, `scripts`, `tests`).
- `make check` is the full offline CI gate (lint, format, types, pytest, pip-audit,
  `dbt build --target duckdb`, `terraform fmt/validate`); `make test` is pytest alone;
  targeted: `uv run pytest {path}`.
- No em dashes anywhere in the repo, docs or code comments.

## Git Workflow

- One branch per issue, named by Linear's `branchName` (`noahwinsdev/qnt-480-...`). One PR per issue,
  **squash merge**, branch deleted: `gh pr merge {pr} --squash --delete-branch`.
- Commit format (enforced by `.githooks/commit-msg`; enable with
  `git config core.hooksPath .githooks`):
  `QNT-123: type(scope): description`, `type` ∈ `feat|fix|refactor|test|docs|chore`.
  WIP: `QNT-123: type(scope): wip - description`. Meta work with no issue may use bare
  `docs: …` / `chore: …`. Plain `QNT-123: wip:` is rejected.
- `main` is branch-protected on GitHub (public since 2026-09-17): the `ci` workflow's `checks`
  job is a required status, linear history, no force pushes; admins are exempt so meta
  commits can still land directly. `.githooks/pre-push` is the local fast-fail that refuses
  direct pushes to `main`. Ticket work goes through a PR; meta `docs:`/`chore:` commits push
  with `ALLOW_MAIN_PUSH=1 git push`.
- PR title `QNT-123: <title>`; body contains `Closes QNT-123`; no tool-generated footers, ever.
  Every execution AC in the PR needs a Command + Output receipt (see `.github/PULL_REQUEST_TEMPLATE.md`).
- Tracker: Linear, team Quant, project **Hyperlake**. Issue IDs match `QNT-\d+`.
- Ticket structure: see the flow package's `method/conventions.md` ("Ticket structure"), reference
  it, don't restate it.


## Environment

- **Local / CI** = DuckDB target, no AWS credentials. CI must stay runnable with zero cloud access.
- **Cloud** = Athena target in ap-northeast-1, reached from GitHub Actions via the OIDC role or
  from a developer session with AWS credentials (`.env.example`: `AWS_PROFILE`, `AWS_REGION`).
  Requester-pays reads on the archive buckets need `s3:GetObject` and `RequestPayer=requester`.
- IAM/OIDC changes are verified by exercising the role (e.g. dispatching `dbt-run.yml`), never by
  a local apply under personal credentials (QNT-473).
- No long-lived prod host: "deployed" means a live session is up. `profile.deploy.deployed_sha`
  and `runtime_id` are still empty; `rollback` is empty by design (a bad session is destroyed and
  re-applied).

## Working Docs

- Read first: `docs/prd.md` (scope, cost model, settled decisions, OQ-1), then
  `docs/project-requirement.md` (spec by phase) and `docs/project-plan.md` (tracker twin of Linear).
- `docs/architecture/system-overview.md`: how the system works *now*; updated by `flow-change-scope`
  and `flow-retro`, never speculatively.
- `docs/decisions/`: ADRs; every significant decision terminates in one (PRD NFR-6). Index in
  `docs/INDEX.md`. Template: `docs/decisions/TEMPLATE.md`.
- `docs/spikes/`: measured findings; `docs/guides/ops-runbook.md`, grep-first failure catalog;
  `docs/guides/dev-workflow.md`, how the flow skills chain.
- `docs/AC-templates.md`: implicit acceptance criteria appended by diff path.
- Execution-AC keywords (never code AC): populated, backfill, no duplicates, returns, queryable,
  reconciles, deployed, destroyed, in athena, visible, healthy.

## Common Commands

- Gate: `make check`
- Infra: `make tf-apply-persistent` / `tf-destroy-persistent`, `make tf-apply-ephemeral` /
  `tf-destroy-ephemeral`, `make tf-drift-check`, `make audit-teardown`
- Batch: `make backfill FROM=<day> TO=<day>`, `make dbt-run ARGS="..."`, `make iceberg-maintain`
- Session: `make session-up LABEL=<prefix>` → stream → `make session-down` → `make heal` →
  `make recon`
- Query: `make bronze-query DT_FROM=<day>` (never query bronze without a `dt` bound)
- Costs: `make cost-backfill`, `make cost-report`
- Docs: `make docs-check`, `make demo-runbook-check`, `make dbt-docs`, `make dbt-demo-fail`
