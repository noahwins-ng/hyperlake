# Demo Runbook

PRD Phase 4 deliverable (§7, §10 OQ-4 amended): the project's demo artifact, real measured
command + output, not a recording. Covers the streaming path (`session-up` → stream →
`heal` → `recon` → query) plus the data-quality failure demo. The batch/backfill path's
own timed walkthrough ("reproduce in 15 minutes") is in
[`docs/guides/bootstrap.md`](guides/bootstrap.md), this runbook assumes that layer
already exists and demos the live/streaming layer on top of it.

Every command below is real: either a `make` target (`Makefile`) or one of the committed
sample queries in [`docs/queries/`](queries/). `make demo-runbook-check` (`scripts/
demo_runbook_check.py`) asserts that stays true (AC2).

## Prerequisites

- AWS credentials for `ap-northeast-1` (`aws sts get-caller-identity` succeeds).
- `infra/main/persistent` already applied (the data bucket + Glue catalog + Athena
  workgroup, see `docs/guides/bootstrap.md`).
- `gh` authenticated (session-down/heal/recon dispatch `dbt-run.yml` via `scripts/
  gh_run.sh`).

## Steps

Measured live 2026-09-12, session `qnt-468-20260912134330` (deliberately short, a full
~4h reference demo scales the stream step up; every other step's mechanics and timing are
unaffected by session length). Cost/timing detail: `docs/guides/ops-runbook.md`.

| # | Step | Command | Expected | Actual (2026-09-12) |
|---|------|---------|----------|----------------------|
| 1 | Start the session | `make session-up LABEL=<label>` | ~1 min (Terraform apply of the ephemeral stack: Kinesis, Firehose, ECS service, backfill primitives, reaper schedule; ~30 resources) | **69.5s**; `qnt-468-20260912134330` |
| 2 | Stream | (nothing to run; the ingester + Firehose write to bronze on their own; watch `aws logs tail /ecs/hyperlake-ingester --region ap-northeast-1 --follow`) | Firehose buffers 60s/64MB, so the first bronze object lands within ~1-2 min; a real demo streams for the intended session length (PRD: ~4h reference session, ≈$0.50-1) | **14m32s** streamed (13:43:30–13:58:02 UTC), `put_records` succeeding continuously across all 5 watchlist coins, 0 disconnects; first bronze object landed within ~2 min |
| 3 | End the session | `make session-down` | ~2-3 min (stop the ingester, wait `DRAIN_SECONDS=120` for the Firehose buffer to flush, destroy the ephemeral stack, dispatch `dbt-run.yml`, `iceberg-maintain`) | **3m16.6s**, exit 1; dbt-run dispatch failed (see "What can go wrong"); the ephemeral stack (33 resources) was still destroyed and the manifest still committed, so nothing billable was left running. Confirmed by a follow-up `terraform plan` in `infra/main/ephemeral`: `Plan: 33 to add, 0 to change, 0 to destroy`; nothing left live |
| 4 | Heal | `make heal SESSION=<session_id>` | No-op ("no unhealed gaps, nothing to do") on a clean session; it exits before touching AWS. On a session with a recorded gap: re-applies the backfill primitives, re-runs the Step Functions Map for the covering hours, re-runs `dbt-run`, re-runs recon (own AC3, next row) | **0.4s**; `no unhealed gaps, nothing to do` |
| 5 | Reconcile (G3) | `make recon SESSION=<session_id>` | Only succeeds once the session spans a *landed* archive hour (`hyperlake.recon.reconcilable_window`: the session's leading/trailing partial hours are excluded, and the covered hour's archive file must have already landed, ~1h after that hour closes). A short session run right after streaming will hit this; see "What can go wrong" below | **0.1s**, exit 1; `no reconcilable hour yet -- archive still lagging` (expected: this session never reached a closed+landed hour) |
| 6 | Query | `make bronze-query DT_FROM=<today>` (bronze); the exactly-once / OHLCV shapes from `docs/queries/silver.sql` / `docs/queries/gold.sql`, `WHERE`-scoped to the session window (silver/gold, run in the Athena `hyperlake` workgroup) | Athena queries return in a few seconds once the workgroup is warm | bronze **2.0s** (2,197 `source='ws'` rows); silver **4.7s** (`row_count=2157, distinct_tid=2157`; exactly-once over the session window, the 40-row gap from bronze is at-least-once duplicates dbt's merge resolved); gold **3.2s** (real `ohlcv_1m` bars for BTC, e.g. `2026-09-12 13:44:00 open=77264.0 close=77261.0 volume=4.40628`) |

Full G3 pass reference (this session's stream was too short to reach a landed hour):
`docs/spikes/2026-09-11-qnt466-g3-live-replay.md`, a real completed session
(`qnt-466-20260911124320`) reconciled `ws_only=0`, `backfill_only=13577` (all but one
inside the recorded gap), `both=132823` over a full landed hour.

## Data-quality failure demo

`make dbt-demo-fail` points the silver source at a fixture (`dbt/fixtures/
bronze_trades_demo_fail.parquet`) with one row carrying an invalid `side` ('X') instead of
the normal sample. Local/offline (duckdb target), no live session needed.

```
$ make dbt-demo-fail
...
ERROR: in test accepted_values_trades_side__A__B (models/silver/_silver.yml)
  Got 1 result, configured to fail if != 0
...
Done. PASS=34 WARN=0 ERROR=1 SKIP=40 NO-OP=0 REUSED=0 TOTAL=75
make: *** [dbt-demo-fail] Error 1
```

Measured 2026-09-12: **4.4s**. The 40 SKIPs are every gold model + test downstream of
`silver.trades`, dbt's own DAG gating, not a project-specific guard, but exactly the
"failures are visible" behavior QNT-464 AC2 wanted for the demo.

**Recovery:** re-run the normal build, it points back at the clean sample fixture.

```
$ make dbt-build
...
Done. PASS=75 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=75
```

Measured 2026-09-12: **4.6s**.

## What can go wrong

- **Archive not landed yet (`make recon` / `make heal`).** `recon` exits with `no
  reconcilable hour yet -- archive still lagging` when the session hasn't yet reached a
  closed, landed hour; `heal` exits listing the not-yet-landed hour(s) instead of running
  a partial backfill. Both are loud, not silent. Response: wait, the archive lands ~1h
  after the hour closes (`docs/guides/ops-runbook.md`), and re-run. Demo framing: this is
  expected, not a bug, on any session shorter than ~1h past a clock-hour boundary; a real
  ~4h demo session clears this naturally.
- **Session was reaped.** If `MAX_SESSION_HOURS` (default 6) elapses with nobody running
  `session-down`, the dead-man's-switch reaper stops the ingester and marks the session
  itself. `make session-down` detects the reap marker and finalizes normally, see the
  ops runbook's "A session was reaped" entry for the full detail and the recovery command
  (`MAX_SESSION_HOURS=8 make session-up` for a longer-than-default demo).
- **`session-up` refuses to start.** Exits loud if a prior session's manifest has no `end`
  (unfinished/forgotten) or the ephemeral Kinesis stream is still in Terraform state, run
  `make session-down` against the prior session first.
- **`session-down`'s `dbt-run` dispatch fails with `No ref found for: <branch>`** (hit live
  2026-09-12). `gh workflow run` pins `--ref` to the caller's current branch (QNT-466 fix,
  so a feature-branch run never silently checks `main` instead), but that ref has to exist
  on the remote first. Running the runbook from a freshly-checked-out feature branch that
  hasn't been pushed yet hits this every time. Response: `git push -u origin
  <branch>`, then re-run, `session-down` already destroyed the ephemeral stack and
  committed the manifest by this point (AC5), so nothing is left dangling; catch silver up
  with `make dbt-run ARGS="-f vars='{\"freshness_window_start\": \"<start>\",
  \"freshness_window_end\": \"<end>\"}'"` using the manifest's own `start`/`end`.

## Cost: estimate vs. actual

PRD §8's per-session model (Fargate + Kinesis + Firehose + Athena maintenance,
resource-hours × ap-northeast-1 list price) against what real sessions have actually cost
(`costs/sessions.csv`, backfilled from Cost Explorer once its 24h tag-activation lag
clears):

| | PRD estimate (§8, ~4h reference session) | Measured actual (8 finalized sessions) |
|---|---|---|
| Per-session cost | ~$0.50-1 | **$0.27 average, $0.29 highest** |
| Idle (no session running) | < $2/month | < $1/month |

The gap is mostly the PRD's `OVERHEAD_HOURLY_USD` margin (CloudWatch Logs ingestion, S3
request charges, per-resource billing minimums) coming in lower in practice than the
padded estimate, see `src/hyperlake/session.py` for the estimate's own breakdown, and
`docs/guides/ops-runbook.md`'s "Ad-hoc Athena queries" entry for the one real cost
incident traced to unbounded bronze queries rather than session overhead. This session's
own estimate-vs-actual: `cost_estimate_usd` **$0.04** (a ~14.5 min session, scaled from
the same per-hour model, `costs/sessions.csv` row `qnt-468-20260912134330`);
`cost_actual_usd` pending Cost Explorer's 24h tag-activation lag, backfilled later by
`make cost-backfill`.
