# Hyperlake: Plan

The narrative tracker. One checkbox item per ticket, grouped by phase (= Linear milestone).
`ship` ticks an item when its ticket merges; `change-scope` adds/removes items when scope shifts;
`sync-plan` reconciles this against the tracker. Every shipped ticket MUST appear here, the plan
is what a reader who opens the repo cold uses to understand what was built and why.

> Item format: `- [ ] <ISSUE-ID>: <short title>` with optional sub-bullets for deliverables or a
> `**Triggered by:**` note explaining why the ticket exists.

> Linear project: [Hyperlake](https://linear.app/noahwins/project/hyperlake-5268252e90c5) (team Quant).
> Seeded 2026-09-04 by flow-plan-project from [`prd.md`](prd.md) v0.8. Cycle 1 = cycle #21.

### Phase 0: Scaffold

Goal: cost guardrails and the toolchain exist before any data resource does.

- [x] QNT-444: chore(repo): Python + Terraform toolchain, CI skeleton, Makefile
  - uv/pyproject, ruff, pyright, pytest; `make lint types test tf-check`; `ci.yml` on every PR, no AWS creds
  - fills `workflow-profile.yaml` `verify.*`
- [x] QNT-445: feat(infra): Terraform bootstrap, state backend, OIDC role, budget alarm, cost allocation tag
  - `infra/bootstrap/` (local state): S3 state bucket, DynamoDB lock, GitHub OIDC role, Budgets $10 alarm + $15 create-deny action
  - `project` cost allocation tag Active; `infra/main/` root on S3 backend with `persistent/` + `ephemeral/` split
  - `docs/guides/bootstrap.md`
- [x] QNT-446: feat(dbt): two-target dbt project skeleton compiling on DuckDB in CI
  - `duckdb` / `athena` targets, one materialization macro (ADR-002), placeholder model + gold-safe fixture
- [x] QNT-447: feat(costs): costs/ log schema and make cost-backfill
  - `costs/sessions.csv` with `cost_estimate` / `cost_actual` / `pending|final`; Cost Explorer backfill by tag

### Phase 1: Lakehouse (batch)

Goal: archive → Lambda backfill → bronze Parquet → silver Iceberg via `dbt-run` → queryable in Athena.

- [x] QNT-448: test(spike): close OQ-1, tid parity between WS capture and official hour file, ADR-005
  - the last OQ-1 gate; freezes the bronze data model; blocks the rest of Phase 1
- [x] QNT-449: feat(core): shared hyperlake package, envelope schema, watchlist config, partition-name helper
  - `config/watchlist.yaml`; envelope pyarrow/Glue schema; `xyz:SP500` → `xyz_SP500`; `dt` from event time
- [x] QNT-450: feat(infra): persistent layer, data bucket, Glue database, bronze trades_raw with partition projection, Athena workgroup
- [x] QNT-451: feat(backfill): Lambda hour-file reader, stream LZ4, filter watchlist, collapse fills, write bronze at deterministic key
  - H/H+1 boundary rule, fill-pair collapse, `coin=/dt=/source=backfill/hour=H.parquet` idempotent keys
- [x] QNT-452: feat(backfill): Step Functions fan-out over hour list + make backfill, 1-day sample timed
  - Map state, retry/catch; G1 one-day timing; 30-day backfill cost recorded
- [x] QNT-453: feat(dbt): silver trades, Iceberg incremental merge on tid with source precedence and first_seen_source
  - `source_rank` (backfill wins), `dt` lookback, insert-only `first_seen_source`; sample queries for bronze + silver
- [x] QNT-454: feat(ci): dbt-run workflow with run_key completion contract, iceberg-maintain target
  - ADR-001 runtime; `scripts/gh_run.sh` (run_key, 20-min timeout, loud failure); `make iceberg-maintain`
- [x] QNT-473: fix(infra): OIDC role's Glue policy scoped to real db names, full dbt-athena IAM action set
  - found while verifying QNT-454 AC4, the OIDC role's Glue permissions never actually covered
    bronze/silver/gold (scoped to an unused `hyperlake*` prefix); also imported the `silver` Glue
    database into Terraform state (it existed live but was hand-created, never `terraform apply`d)

### Phase 2: Streaming

Goal: Fargate ingester → Kinesis → Firehose → the same bronze; scripted session lifecycle with manifest; bounded blast radius.

- [x] QNT-455: feat(spike): Kinesis + Firehose Parquet landing spike, envelope schema, dynamic partitioning, destroy
  - Phase 2 learning spike; ADR-004 go/no-go
- [x] QNT-456: feat(ingester): Python WebSocket ingester, trades subscription, envelope, PutRecords, reconnect with gap recording, self-exit
- [x] QNT-457: feat(infra): Fargate ingester service + Firehose in the ephemeral layer, G2 measured
  - default VPC, public IP, egress-only SG; image tagged by commit SHA; emission→bronze < 3 min
- [x] QNT-458: feat(session): make session-up / session-down with committed manifest and cost_estimate
  - `sessions/<id>.json`; Firehose drain wait; `dbt-run` + `iceberg-maintain`; costs row `pending`
  - `session-up` preflight: refuse to start if the latest manifest has no `session_down_at` /
    isn't `reaped: true`, or `terraform state list` on the ephemeral layer shows the Kinesis
    stream already present, guards against an overlapping/forgotten session outrunning the
    reaper's 6h bound
- [x] QNT-459: feat(session): session reaper, one-time EventBridge Scheduler + Lambda, drift-tolerant apply/destroy verified
  - `session-down` treats a detected reap as an anomaly, not a silent clean stop: loud warning
    naming the session id + `reaped_at`, and the `costs/` row it appends is marked
    reaper-terminated, makes a fired reaper visible instead of looking like a normal teardown

### Phase 3: Convergence + transforms

Goal: G3 proven at bronze, gaps healed, seam tested on Athena, gold marts with data-quality gates.

- [x] QNT-460: feat(dbt): recon_trades over bronze with gap-aware G3 tests
  - `ws_only = 0`; every `backfill_only` inside a manifest gap (ADR-003)
- [x] QNT-461: feat(session): make heal, backfill unhealed gap hours + trailing hour, re-run dbt-run, flip healed
- [x] QNT-462: test(dbt): Athena seam test, seam_test schema, tag:seam, runs on every push to main
- [x] QNT-463: feat(dbt): gold marts, ohlcv_1m/1h/1d, volume_daily, liquidations_daily with OHLCV invariant tests
- [x] QNT-464: test(dbt): silver contract tests, schema, freshness, volume; gold gated on silver tests
  - includes `make dbt-demo-fail` for the demo
- [x] QNT-465: feat(backfill): Reservoir fallback reader via column mapping, layout-pinned and loud on drift
- [x] QNT-466: test(e2e): G3 replay on a live session, stream, heal, recon passes, manifest committed
  - the phase's demoable state; first fully populated manifest

### Phase 4: Presentation

Goal: a hiring manager can absorb the project in ten minutes; a stranger can reproduce it.

- [x] QNT-467: docs(readme): README with architecture diagram, per-layer sample queries, and bootstrap path
  - carries the timed G1 (< 15 min) reproduction; fills `architecture/system-overview.md`
  - live 2026-09-11: bootstrap/persistent no-op re-apply → ephemeral apply → 1-day backfill →
    dbt-run → Athena query in 8m53s; detail in `docs/guides/ops-runbook.md`
- [x] QNT-468: docs(demo): demo runbook with timings, session-up → stream → heal → recon → query
  - live 2026-09-12: session `qnt-468-20260912134330`, session-up 69.5s, ~12 min stream;
    recon hit the expected "archive not landed" path on a short session (full G3 pass
    reference: QNT-466's spike)
- [x] QNT-469: docs(demo): cost report + reconciliation
  - cost report reconciles the Cost Explorer all-time total against `sum(cost_actual)` in
    `costs/sessions.csv`, explaining any gap, added in the Phase 3 retro (QNT-476 cost incident)
  - recorded-video requirement dropped (scope change 2026-09-15, OQ-4 amended), `docs/demo-runbook.md`
    (QNT-468) is the demo artifact
- [x] QNT-470: feat(dbt): dbt docs generated in CI, lineage graph captured for README
  - live-Pages AC dropped (scope change 2026-09-16, modify), GitHub Free can't serve Pages from
    a private repo; lineage graph ships as a static screenshot instead
  - Cost Explorer screenshot AC dropped (scope change 2026-09-16, modify), the existing
    `costs/sessions.csv`/`docs/costs.md` reconciliation tables already carry the cost proof
- [x] QNT-478: docs(readme): restructure README as a shareable portfolio landing page
  - problem statement, proof-first layout, design decisions, stack/repo tables, run/verify/tear-down,
    testing + CI, "what I would do differently"; internal vocabulary (G*/OQ-*/QNT-*/phase) swept out
  - proof image replaced by the runbook's measured bronze/silver/gold outputs (qnt-468, 2026-09-12);
    cost report regenerated with qnt-468's actual (12/12 sessions final, avg $0.30)

### Phase 5: Production readiness

Goal: the pipeline measures and reports its own quality and is shown to recover from loss, at this
scale and with no new AWS services. Added by scope change 2026-10-03.

- [x] QNT-487: feat(dbt): daily data-quality mart with a stated freshness target
- [x] QNT-488: feat(dbt): measure archive-vs-feed corrections and prove a late correction reaches gold
- [x] QNT-489: docs(readme): reconciliation result visual and a "not in scope at this scale" list
- [x] QNT-491: chore(ops): measured recovery drill, rebuild silver and gold from bronze

### Ops & Reliability  <!-- perpetual milestone: hardening that cuts across phases -->

- [x] QNT-471: chore(ops): post-destroy audit, list any live project=hyperlake billable resources and fail loudly
  - wired as the last step of `session-down`
- [x] QNT-474: chore(ops): Terraform state drift check on the persistent layer
  - compares live project=hyperlake resources (Glue databases/tables) against `terraform state list`; fails on drift in either direction
  - **Triggered by:** QNT-473 (Phase 1 retro), `silver` Glue db was hand-created, drifted undetected
- [x] QNT-475: chore(infra): bump hashicorp/aws provider to ~> 6.0 across all three Terraform roots
  - `versions.tf` + lockfile in bootstrap, persistent, ephemeral; `terraform plan` proven zero-diff against real state
  - **Triggered by:** Dependabot PR #11, which bumped only `infra/bootstrap` and would have drifted the three roots onto different provider majors
- [x] QNT-476: chore(ops): bronze ad-hoc query guardrail, bounded dt filter wrapper
  - `make bronze-query DT_FROM=...` requires a bounded `dt` lower bound and refuses to run unfiltered; safe pattern documented in `docs/guides/ops-runbook.md`
  - **Triggered by:** cost investigation (2026-09-09) tracing ~$0.62 of ~$0.78 total spend to unbounded ad-hoc scans against bronze's partition-projected table; deferred from QNT-460 scoping
- [x] QNT-477: fix(infra): declare + import seam_test Glue database, tf-drift-check flags it as hand-created
  - `seam_test` (auto-created live by QNT-462's seam job) declared in `glue.tf` and `terraform import`ed, same fix shape as QNT-473's `silver` import
  - **Triggered by:** QNT-474's scheduled `tf-drift-check` catching real drift for the first time, 2026-09-10
- [x] QNT-479: chore(ops): portfolio-hygiene CI check, no stray ticket-id comments in src/hyperlake or dbt/, no real AWS account id, no negative cost-report figures
  - **Triggered by:** Phase 4 retro invariant audit, three hygiene defects (the real AWS account id briefly in `tests/test_audit_teardown.py`, a negative `$` figure in the README cost block, and stray `QNT-` comments the `cc2e8c1` sweep could reintroduce into `src/hyperlake`/`dbt/`) were all caught only by manual review the night before the repo went public, none by `make check`
  - `make portfolio-lint` (new); AC3 needed no new code, already pinned by `tests/test_cost_report.py`'s existing negative-headline regression test
- [x] QNT-482: fix(dbt): Iceberg tables live under athena-results/ and are expired after 7 days
  - **Triggered by:** the seam job failing with `ICEBERG_MISSING_METADATA` on every push to `main`
    from 2026-09-24; dbt-athena had written every silver/gold/recon/seam table and seed under the
    7-day-expiring `athena-results/` prefix, and all of them were deleted
  - `s3_data_dir` → `warehouse/` via the `DBT_ATHENA_S3_DATA_DIR` repo variable, guarded in
    `dbt-run.yml`; seeds Iceberg (the enforced workgroup places Hive tables under
    `athena-results/`); silver/gold rebuilt from bronze (29,086,722 trades, exactly-once)
- [x] QNT-481: test(dbt): run the Athena seam test on PRs that touch dbt/
  - `seam-pr.yml`: per-PR `seam_test_pr<N>` schema, dropped after the run; green no-op for non-`dbt/` and fork PRs; ADR-002 amended
  - **Triggered by:** README "What I would do differently", a bad merge change was only caught after merge to `main`
- [x] QNT-480: feat(ingester): per-coin gap tracking and a gap-injection flag
  - **Triggered by:** QNT-466's residual, one ETH trade 534 ms past a BTC-closed gap_end
    (one shared gap timeline for all coins), and a disconnect that took three NACL edits to induce
  - Per-coin `GapTracker`; `--inject-disconnect-after-s/-for-s` (Terraform vars, default off);
    live proof `qnt-480-20260929124521`: ws_only = 0, no archive-only trade outside a per-coin gap
  - Found live: the seed wrote HIP-3 gap coins as `xyz:SP500` while bronze's `coin` is the
    partition value `xyz_SP500`; the seed now normalises via `coin_partition_value`
- [x] QNT-485: fix(dbt): silver/gold carry the HIP-3 partition value (xyz_SP500) instead of the exact coin name
  - **Triggered by:** QNT-480's live recon: on Athena bronze `coin` is only the Hive partition
    key, so silver and gold inherited `xyz_SP500` against the PRD's exact-name rule
  - Silver takes `coin` from `raw_payload` via a `json_string` helper (ADR-002 amended); fixtures
    now mirror Athena's bronze shape; guard `assert_hip3_coin_exact_name` on silver + every gold
    model; silver rebuilt (60-day lookback), still exactly-once at 29,200,254 trades
- [x] QNT-484: test(dbt): pin drop_seam_schema's prefix guard; run seam-pr when its own wiring changes
  - **Triggered by:** QNT-481's review advisories
  - `tests/test_drop_seam_schema_guard.py` (offline, in `make check`); `seam-pr.yml` also runs when it or `infra/bootstrap/oidc.tf` changes
  - Review follow-up: guard tightened to `seam_test_pr<digits>` (refuses `seam_test_prod`)
- [x] QNT-486: fix(deps): bump urllib3 to 2.8.0, pip-audit fails on PYSEC-2026-4175/4176/4177
  - **Triggered by:** `make audit` failing the Dependabot PRs #79/#80 on 2026-10-02; `main`'s lock pinned 2.7.0, so every new PR would fail `checks`

## Parking lot (no tickets: reopen the PRD before building)

Schema-evolution demo · L2 order book / funding / HyperEVM · all-markets scope · S3 Tables / MSK variant ·
always-on dashboard · Step Functions → dbt dispatch (ADR-001 stretch).
