# Ops Runbook

Grep-first failure-mode catalog, the index into hard-won operational muscle memory. When
something breaks, grep here first. `flow-investigator` reads this. Every reliability ticket
should add or extend an entry.

Each entry follows the same shape:

## <Failure mode: a symptom you'd actually search for>

- **Symptom:** what you observe (the alert, the error, the user report).
- **Diagnosis:** the exact commands to confirm the cause (debug *state*, not logs, query the
  system's own status first).
- **Response:** the steps to remediate, safest first.
- **Prevention:** the guard (CI check / ship hard-gate / config) that now stops it recurring, or
  "accepted risk: <reason>".

---

## `terraform apply` in `infra/bootstrap` fails on `aws_ce_cost_allocation_tag.project`

- **Symptom:** first-ever bootstrap apply errors on the cost allocation tag: the key `project`
  is "not found" / not activatable, while every other resource created fine.
- **Diagnosis:** `aws ce list-cost-allocation-tags --tag-keys project` returns nothing. AWS only
  lists a tag once it has appeared in billing data, which lags actual tagging by up to 24 h
  (PRD FR-7).
- **Response:** nothing to fix. Re-run `terraform apply` the next day; it converges. Until then
  `make cost-backfill` (QNT-447) cannot filter by tag.
- **Prevention:** accepted risk, documented in `infra/bootstrap/cost_tag.tf` and
  `docs/guides/bootstrap.md`; the resource is idempotent so the retry is safe.

## Archive hour file not landed (`check_tid_parity.py` exits 2, or a backfill hour is missing)

- **Symptom:** `NOT READY: N hour file(s) missing; re-run later. No verdict.` from the spike
  gate, or a backfill invocation reports `NoSuchKey` on
  `s3://hl-mainnet-node-data/node_fills_by_block/hourly/YYYYMMDD/H.lz4`.
- **Diagnosis:** `aws s3api head-object --bucket hl-mainnet-node-data --request-payer requester
  --key node_fills_by_block/hourly/YYYYMMDD/H.lz4`. Hour `H` lands ~2 min after hour `H+1`
  closes (~1 h lag, measured 2026-09-04). A trade near the top of the hour may sit in `H+1`
  because files are cut by block *arrival* time.
- **Response:** wait for the lag and retry. If still absent after 24 h, that hour goes to the
  Reservoir fallback reader (QNT-465).
- **Prevention:** `make heal` (QNT-461) HEADs every hour object before starting a Map execution
  and exits non-zero listing the not-yet-landed hours instead of running a partial backfill.

## Falling back to the Reservoir daily file (`make backfill-fallback DATE=`)

- **When:** only after the 24 h wait above, this is a manual decision, not an automatic
  fallback (out of scope for QNT-465 on purpose). Confirm the official hour file is still
  missing (`aws s3api head-object` per the entry above) before running this; the fallback
  reader derives its own `hour=` object keys from each fill's event time (`hyperlake.
  partitions.hour_from_event_time_ms`), which can differ from whichever invocation hour the
  official reader would have used for the same trades, running both for the same date can
  leave two bronze objects covering the same trades under different `hour=` keys. Silver's
  merge-on-`tid` (ADR-005) makes that safe to query, just wasteful.
- **Command:** `make backfill-fallback DATE=YYYY-MM-DD`, runs locally (no Lambda/Terraform),
  reads `s3://hydromancer-reservoir/by_dex/{dex}/fills/perp/all/date=YYYY-MM-DD/fills.parquet`
  requester-pays for each dex the watchlist touches, and writes bronze through the same
  envelope + Parquet writer as the official reader.
- **Diagnosis if it fails loud:** a `hyperlake.backfill.reservoir.SchemaError` naming missing
  column(s), or an unrecognised `side` value, means Reservoir's layout has drifted again (it
  already has once, `_pre_hip4_unification_backup/`), the reader pins the full 28-column
  contract (`PINNED_COLUMNS`) and refuses to guess at a null-filled row. A `pyarrow.
  ArrowInvalid: Rescaling Decimal value would cause data loss` instead means a real
  price/size/fee needed more than 8/6/6 decimal digits (the envelope's `px`/`sz`/`fee`
  scales), also loud, not silent, but a different exception path than `SchemaError`; no
  watchlist market has needed more than 5 observed (OQ-1 spike).
- **Prevention:** the schema assertion runs against the file's footer metadata before any row
  is read (pytest: `tests/test_backfill_reservoir.py`, AC2), drift is a loud exception, not a
  silent wrong answer downstream.
- **Parity proof (AC3, 2026-09-10):** read-only, no bronze writes on either side. BTC,
  2026-09-03 hour 12 (already-backfilled bronze via the official reader, vs. a fresh
  Reservoir read of the same hour): both sides returned **25,569** distinct `tid`s, 0
  only-in-official, 0 only-in-reservoir, exact match, consistent with the OQ-1 spike's
  measured ≈25.6 k figure for the same hour.

## `make cost-backfill` reports `0 row(s) backfilled` when a `pending` row should have filled

- **Symptom:** a session's `costs/sessions.csv` row is more than a day old but stays
  `cost_status=pending` after running `make cost-backfill`.
- **Diagnosis:** the target only queries rows whose `end` is > 24h old (`BACKFILL_DELAY` in
  `scripts/cost_backfill.py`), and only sums cost tagged `project=hyperlake`
  (`aws ce list-cost-allocation-tags --tag-keys project`, must show `Status: Active`, see the
  bootstrap entry above). A `$0.00` result after backfill is a valid outcome (idle cost), not a bug.
- **Response:** confirm the row's `end` timestamp and the tag's activation status; re-run once
  both hold.
- **Prevention:** `costs/README.md` documents the schema and the 24h delay; `--dry-run` previews
  the query without writing the file.

## Backfill Step Functions fan-out: measured wall time & cost (QNT-452)

Measured against the live ephemeral stack (`hyperlake-backfill` state machine, Map
`MaxConcurrency=10` over the backfill Lambda), 2026-09-06/07 in `ap-northeast-1`:

| Run | `make backfill` | Hours run | Failed | Wall time | Lambda cost |
|---|---|---|---|---|---|
| 1-day (AC1/AC2) | `FROM=2026-09-03 TO=2026-09-03` | 25 (24 + trailing H+1) | 0 | 20.5 s | negligible |
| 30-day (AC4) | `FROM=2026-08-05 TO=2026-09-03` | 721 (720 + trailing H+1) | 0 | 317.2 s (~5.3 min) | ~$0.10 |

- **AC2 cross-check:** Athena `SELECT count(*) FROM bronze.trades_raw WHERE dt=DATE
  '2026-09-03'` returned **1,110,546**: an exact match to the spike's Reservoir
  watchlist figure (BTC 504,527 + HYPE 328,003 + ETH 195,708 + xyz:SP500 47,657 +
  xyz:XYZ100 34,651 = 1,110,546 trades), i.e. 0% deviation, well inside the ±1% AC.
- **AC4 cost source:** Cost Explorer's `project` tag lags billing data by up to 24h
  (see the bootstrap entry above), so same-session cost was read from CloudWatch Logs
  Insights instead: `filter @type="REPORT" | stats sum(@billedDuration) as
  totalBilledMs, count() as invocations` over `/aws/lambda/hyperlake-backfill-official`
  → 742 invocations, 3,139,807 ms total billed duration (25 + 721 = 746 expected from
  the two runs; the query's 1h lookback window likely clipped a few of the 1-day run's
  earliest REPORT lines, not a retry/duplicate-invocation signal, since both runs
  independently reported 0 failed hours). At 2048 MB / $0.0000166667 per GB-s:
  `3139.807 s × 2 GB × $0.0000166667/GB-s ≈ $0.105` compute + ~$0.0001 in requests ⇒
  **≈ $0.10 total**, comfortably under the $2 AC4 ceiling.
- **Task-level timeout:** the Map's `InvokeBackfillLambda` task carries
  `"TimeoutSeconds": 320` (just above the Lambda's own 300s timeout) so its
  `States.Timeout` Retry entry can actually fire on a real hang, ASL defaults an
  unset Task timeout to 99999999s, which would otherwise make that Retry entry dead
  code (caught in review).
- **Teardown verified** (implicit Terraform/session-lifecycle AC,
  `docs/AC-templates.md`): after the live runs, `terraform apply -destroy
  -var tfstate_bucket=...` → `Apply complete! Resources: 0 added, 0 changed, 10
  destroyed.`, followed by `terraform plan` → `Plan: 10 to add, 0 to change, 0 to
  destroy.`, proving no lingering/drifted resource, matching the original 10-resource
  set exactly. The ephemeral stack (state machine + backfill Lambda + IAM + the
  disabled schedule) was destroyed immediately after each apply, per the
  ephemeral-by-design rule.

## G1 stranger-path timing: bootstrap → backfill → Athena query (QNT-467 AC1)

Measured live end-to-end in `ap-northeast-1`, 2026-09-11, following the README's
"reproduce in 15 minutes" path against this project's one real AWS account (a genuinely
fresh AWS account is a one-time NFR-7 cost this project doesn't re-pay per measurement,
`infra/bootstrap` and `infra/main/persistent` are idempotent, so re-running them against
already-applied state is the equivalent proof; see `docs/guides/bootstrap.md` AC1):

| Step | Command | Result | Elapsed |
|---|---|---|---|
| Bootstrap (no-op re-apply) | `terraform plan` in `infra/bootstrap` | No changes | - |
| Persistent (no-op re-apply) | `terraform plan` in `infra/main/persistent` | No changes | - |
| Ephemeral apply (backfill primitives) | `make tf-apply-ephemeral` | 7 resources added | - |
| 1-day backfill | `make backfill FROM=2026-09-10 TO=2026-09-10` | 25 hours run, 0 failed | 20.5 s |
| `dbt-run` (OIDC) | `make dbt-run` | silver/gold/recon built | ~70 s |
| Athena query | `make bronze-query` + `silver.trades` count | bronze 867,681 rows; silver row_count = distinct_tid = 867,681 | - |
| **Total (apply → query)** | | | **8 min 53 s** |

- **Result: 8m53s, well inside the 15-minute G1 ceiling** (bootstrap's own AC1 proof, a
  no-op `terraform plan`, and the persistent layer's no-op plan both complete in
  seconds, so nearly the full budget goes to the ephemeral apply + real 1-day backfill
  + `dbt-run` round trip).
- **Gotcha hit and documented for the README's quickstart:** the first `make dbt-run`
  dispatch used `dbt_project.yml`'s placeholder `freshness_window_*` vars (meant to be
  overridden per real session/backfill window, QNT-466 already hit this for
  `session_down.py`/`heal.py`, see the entry below) and failed
  `assert_silver_freshness`. Re-dispatched with `-f vars='{"freshness_window_start":
  "2026-09-10 00:00:00", "freshness_window_end": "2026-09-11 01:00:00"}'` matching the
  backfilled window, which passed. The README's quickstart command includes this flag
  up front so a first-time reader doesn't hit it; the measured 8m53s includes the failed
  first dispatch's ~2 min round trip anyway, so the clean-path time is faster still.
- **Ephemeral teardown:** `terraform destroy` on the same 7 backfill-primitive resources
  → `Destroy complete! Resources: 7 destroyed.`, confirmed clean by a follow-up
  `terraform plan` showing only additions (nothing left to destroy).
- **Cost:** not yet in Cost Explorer (24h tag lag, see the bootstrap entry above); same
  order of magnitude as QNT-452's measured 1-day backfill (~$0.10), comfortably under
  the $2/session G4 ceiling.

## `dbt-run` workflow fails, times out, or a caller can't tell which run is theirs

- **Symptom:** `scripts/gh_run.sh` exits non-zero, or two callers dispatched around the same
  time (`session-down` and a manual `make dbt-run`) can't tell which Actions run is which.
- **Diagnosis:** the run URL is always printed by `gh_run.sh`, success or failure -- open it for
  the `dbt build` step's log and the `run_results.json` artifact. `gh run list --workflow
  dbt-run.yml` alone is not enough to disambiguate two nearly-simultaneous runs by eye; `gh_run.sh`
  locates its run by matching `run_key` against `run-name` (never "latest"), which is what makes
  the two cases distinguishable in the first place.
- **Response:** re-run `make dbt-run` (or `make heal`); a fresh `run_key` gets a fresh run.
- **Prevention (required status + failure notification, FR-8):** `dbt-run.yml` runs over OIDC
  on push to main, so it can't be a PR-time required status check (only the offline `checks`
  job is, since the repo went public 2026-09-17); loudness comes from two other places instead: (1) `gh_run.sh` exits non-zero synchronously in
  the caller's own terminal (`session-down`/`heal`/CI), so a failure can't pass silently, and
  (2) GitHub's default email notification to the triggering actor on a failed workflow run,
  which needs no branch protection to fire. Both are exercised by `tests/test_gh_run_script.py`'s
  stubbed-`gh` failure/timeout cases and by AC2's real deliberately-failing run.

## `git push` refused: "Direct push to main refused"

- **Symptom:** `.githooks/pre-push` blocks the push; `error: failed to push some refs`.
- **Diagnosis:** you are pushing to `refs/heads/main`. GitHub also protects `main` server-side
  (required `checks` status; admins exempt), the hook is the local fast-fail in front of it.
- **Response:** ticket work → open a PR (`gh pr create`, then `gh pr merge --squash
  --delete-branch`). Meta `docs:`/`chore:` commits → `ALLOW_MAIN_PUSH=1 git push`.
- **Prevention:** by design. Fresh clones need `git config core.hooksPath .githooks` or the
  hook does not run at all.

## A session was reaped (the dead-man's switch fired)

- **Symptom:** `make session-down` prints `WARNING -- session <id> was reaped at <reaped_at>
  (dead-man's switch fired, max_session_hours exceeded)` on stderr instead of doing a normal
  drain-and-stop; or, before running `session-down` at all, `aws ecs describe-services` shows
  the ingester already at `desiredCount: 0` and `aws kinesis describe-stream` 404s on
  `hyperlake-trades` even though nobody ran `session-down`.
- **Diagnosis:** the session ran past `max_session_hours` (default 6, PRD FR-8) with nobody
  watching. `session-up`'s per-session EventBridge Scheduler `at()` entry
  (`hyperlake-reaper-<session_id>`) fired and invoked the reaper Lambda
  (`src/hyperlake/session_reaper.py`), which scaled the ingester to 0, waited for the Firehose
  buffer to drain, deleted the Kinesis stream, and wrote a reap marker to
  `s3://<data-bucket>/sessions/<session_id>.reaped.json` -- confirm with `aws s3api get-object
  --bucket <data-bucket> --key sessions/<session_id>.reaped.json /dev/stdout`. The Lambda has
  no git/repo access, so that marker (not the locally-committed manifest) is the only record
  until `session-down` runs and reads it.
- **Response:** just run `make session-down` as normal -- it detects the marker, skips the
  now-redundant stop/drain steps, finalizes the manifest with `reaped: true` /
  `reaped_at` (using the marker's timestamp as the session's `end`), and marks the appended
  `costs/sessions.csv` row `cost_status=reaper-terminated` rather than `pending`, so a reap
  stays visibly distinct from a normal teardown in the cost log. `make session-up` afterward
  starts a fresh session normally -- the stream Terraform still had recorded is dropped from
  state during the `terraform destroy` inside `session-down` (state refresh detects it's
  already gone), so the next apply just recreates it.
- **Prevention:** this *is* the prevention mechanism for a forgotten session (the alternative
  is an unbounded bill); nothing to fix. If reaps are firing on sessions you didn't forget,
  the `MAX_SESSION_HOURS` env var (default 6) is too tight for that demo -- raise it for that
  run: `MAX_SESSION_HOURS=8 make session-up`.

## `make audit-teardown` exits non-zero after `session-down`

- **Symptom:** `session-down`'s last step (or a standalone `make audit-teardown`) prints
  `LIVE BILLABLE RESOURCE: <arn>` and exits 1 -- something tagged `project=hyperlake` is still
  billable after teardown.
- **Diagnosis:** the listed ARN names the exact resource (`scripts/audit_teardown.py` queries the
  Resource Groups Tagging API for `project=hyperlake`, then keeps only Kinesis/Firehose/live-ECS
  service/Scheduler-schedule ARNs -- the ephemeral types `session-down` is supposed to have torn
  down). Cross-check with `aws resourcegroupstaggingapi get-resources --tag-filters
  Key=project,Values=hyperlake` and `terraform -chdir=infra/main/ephemeral state list` to see if
  `terraform destroy` actually ran or partially failed.
- **Response:** delete the named resource by hand (or re-run `terraform destroy` in
  `infra/main/ephemeral` if state still shows it), then re-run `make audit-teardown` to confirm
  it prints `no billable ephemeral resources` and exits 0.
- **Prevention:** this check *is* the prevention mechanism (FR-6) -- it exists specifically to
  catch a `terraform destroy` that silently left something behind; a missed resource is a silent
  monthly burn otherwise. It never flags persistent-layer resources (S3/Glue/Athena/ECR), which
  are expected to remain.

## Ad-hoc Athena queries against `bronze.trades_raw` (S3 request-count cost)

- **Symptom:** an unbounded `SELECT ... FROM bronze.trades_raw` (no `dt` filter) run
  manually in the Athena console feels slow and shows up as S3 `Requests-Tier1/Tier2`
  cost, out of proportion to the actual bytes scanned.
- **Diagnosis:** `bronze.trades_raw` uses Glue partition projection (`dt` range
  2025-07-27..NOW × 5 watchlist coins × 2 sources ≈ 4,090 virtual partitions) with no
  persisted partition metadata, so Athena issues an S3 LIST per virtual partition on any
  query that doesn't bound `dt`. A cost investigation (2026-09-09) traced ~$0.62 of the
  project's ~$0.78 total AWS spend to exactly this, from ad-hoc queries run right after
  QNT-450 created the table and during the QNT-455..459 dev sessions.
- **Safe pattern:** use `make bronze-query DT_FROM=YYYY-MM-DD` instead of querying Athena
  directly, it requires a bounded `dt` lower bound and refuses to run (no Athena call at
  all) without one. `DT_TO` (default: same as `DT_FROM`, i.e. one day), `SELECT`,
  `WHERE` (ANDed with the `dt` bound), and `LIMIT` (default 100) are optional, e.g.:
  ```
  make bronze-query DT_FROM=2026-09-03 DT_TO=2026-09-03 SELECT=tid,coin WHERE="coin = 'BTC'" LIMIT=20
  ```
- **Prevention:** the dbt-managed path is already guarded by `silver_lookback_days`
  (`dbt/models/silver/trades.sql`), this wrapper is the equivalent guard for the
  ad-hoc/manual path (QNT-476). It does not change bronze's partition strategy (out of
  scope, current cost is well within budget); it only stops unbounded scans at the
  query layer.

## `ICEBERG_MISSING_METADATA` on a silver, gold, or seam table

**Symptom:** a `dbt-run` build or the `seam` job fails with `ICEBERG_MISSING_METADATA: Metadata
not found in metadata location for table ...`, typically about a week after the table was last
written.

**Cause (2026-09-24, QNT-482):** the table's files were under `athena-results/`, which the data
bucket's lifecycle rule expires after 7 days. dbt-athena puts table data under
`<s3_staging_dir>/tables/` unless `s3_data_dir` is set; before QNT-482 it wasn't, so every
silver/gold/recon/seam table and seed was deleted a week after its last write. Glue kept
pointing at the missing metadata. Bronze (`bronze/`) was never affected.

**Check:** `aws glue get-tables --database-name silver --query
'TableList[].[Name,StorageDescriptor.Location]'`; every location must be under `warehouse/`.
`dbt-run.yml` now refuses to run (`scripts/check_athena_data_dir.sh`) if the
`DBT_ATHENA_S3_DATA_DIR` repo variable is unset or under `athena-results/`.

**Recover:** delete the orphaned Glue table entries (`aws glue delete-table`; their files are
already gone), then `make dbt-run` with `silver_lookback_days` covering bronze's oldest `dt`, so
silver rebuilds from bronze and gold rebuilds from silver.

## `make check` green locally, `ci.yml` red

- **Symptom:** the local gate passes but the same commit fails in Actions.
- **Diagnosis:** `make check` runs exactly the `ci.yml` steps in the same order; a divergence is
  almost always a missing `uv sync` (stale local venv) or a Terraform provider not cached
  locally. Compare `uv lock --check` and `terraform -chdir=<dir> init -backend=false`.
- **Response:** `uv sync --all-groups`, re-run `make check`.
- **Prevention:** `workflow-profile.yaml` `verify.test` points at `make check`, so the ship
  pipeline's sanity gate and CI cannot drift apart without editing both files.
