# Ops Runbook

Grep-first failure-mode catalog — the index into hard-won operational muscle memory. When
something breaks, grep here first. `flow-investigator` reads this. Every reliability ticket
should add or extend an entry.

Each entry follows the same shape:

## <Failure mode — a symptom you'd actually search for>

- **Symptom:** what you observe (the alert, the error, the user report).
- **Diagnosis:** the exact commands to confirm the cause (debug *state*, not logs — query the
  system's own status first).
- **Response:** the steps to remediate, safest first.
- **Prevention:** the guard (CI check / ship hard-gate / config) that now stops it recurring — or
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
- **Prevention:** accepted risk — documented in `infra/bootstrap/cost_tag.tf` and
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

## `make cost-backfill` reports `0 row(s) backfilled` when a `pending` row should have filled

- **Symptom:** a session's `costs/sessions.csv` row is more than a day old but stays
  `cost_status=pending` after running `make cost-backfill`.
- **Diagnosis:** the target only queries rows whose `end` is > 24h old (`BACKFILL_DELAY` in
  `scripts/cost_backfill.py`), and only sums cost tagged `project=hyperlake`
  (`aws ce list-cost-allocation-tags --tag-keys project` — must show `Status: Active`, see the
  bootstrap entry above). A `$0.00` result after backfill is a valid outcome (idle cost), not a bug.
- **Response:** confirm the row's `end` timestamp and the tag's activation status; re-run once
  both hold.
- **Prevention:** `costs/README.md` documents the schema and the 24h delay; `--dry-run` previews
  the query without writing the file.

## `git push` refused: "Direct push to main refused"

- **Symptom:** `.githooks/pre-push` blocks the push; `error: failed to push some refs`.
- **Diagnosis:** you are pushing to `refs/heads/main`. The repo is private on GitHub Free, so
  there is no server-side branch protection; the hook is the local substitute.
- **Response:** ticket work → open a PR (`gh pr create`, then `gh pr merge --squash
  --delete-branch`). Meta `docs:`/`chore:` commits → `ALLOW_MAIN_PUSH=1 git push`.
- **Prevention:** by design. Fresh clones need `git config core.hooksPath .githooks` or the
  hook does not run at all.

## `make check` green locally, `ci.yml` red

- **Symptom:** the local gate passes but the same commit fails in Actions.
- **Diagnosis:** `make check` runs exactly the `ci.yml` steps in the same order; a divergence is
  almost always a missing `uv sync` (stale local venv) or a Terraform provider not cached
  locally. Compare `uv lock --check` and `terraform -chdir=<dir> init -backend=false`.
- **Response:** `uv sync --all-groups`, re-run `make check`.
- **Prevention:** `workflow-profile.yaml` `verify.test` points at `make check`, so the ship
  pipeline's sanity gate and CI cannot drift apart without editing both files.
