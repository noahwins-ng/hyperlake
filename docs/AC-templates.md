# Implicit AC templates

Acceptance criteria that apply to a *class* of change automatically — appended by flow-sanity-check
and flow-review when `git diff --name-only <default_branch>...HEAD` matches a trigger glob, even if
the issue author didn't list them. Referenced by `profile.docs.ac_templates`.

<!-- derived: flow-tailor 2026-09-05, from PRD v1.0's dangerous surfaces + architecture_rules —
     replaces the deployed-service skeleton (no long-lived host here; the risk is cost blast
     radius, silent dedup/partition breakage, and stale reproducibility claims). Re-checked
     2026-09-06 (post-QNT-449/450): infra/**, dbt/models/staging/**, and src/hyperlake/** now
     exist for real. Re-checked 2026-09-07 (post-Phase-1 retro, QNT-473): added the IAM/OIDC
     verification bullet to the Terraform group from a real incident, not a hypothetical — see
     docs/retros/phase-1-lakehouse.md. `sessions/**` (QNT-458) still doesn't exist. -->

## Terraform / session-lifecycle changes

Apply when the diff touches `infra/**`, `sessions/**`, the session-lifecycle `Makefile` targets
(`session-up`/`session-down`/`heal`), or the session reaper.

### Default AC
- `terraform plan` shows only resources tagged `project=hyperlake` — no surprise resource, no
  banned service (NAT Gateway, MWAA, MSK provisioned, OpenSearch, QuickSight) reintroduced.
- `session-down` actually destroys what it created — verified by a follow-up `terraform plan`
  (or the post-destroy audit, QNT-471), not assumed from the script exiting 0.
- Cost guardrail intact: the session's `cost_estimate` is written to `costs/sessions.csv`.
- Every CloudWatch log group the change's Lambda / Fargate / Firehose resources will write to is
  declared in Terraform with `retention_in_days <= 14`. Implicitly created log groups are not in
  state, survive `destroy`, and grow forever — the one leak the post-destroy audit (QNT-471)
  would otherwise find only by accident.
- Any IAM/OIDC policy change (`aws_iam_role_policy`, `aws_iam_policy_document`) is verified by
  actually exercising the role — dispatch `dbt-run.yml`, don't just `terraform apply`/`dbt build`
  under a developer's own broad AWS credentials. QNT-473's Glue policy gap sat wrong for ~46h of
  real build time specifically because nothing did this until the workflow first ran for real.

## CI / dbt-run workflow changes

Apply when the diff touches `.github/workflows/*.yml`.

### Default AC
- `ci.yml` still runs green with **zero AWS credentials** — the offline gate (lint/types/pytest/
  `dbt build --target duckdb`/`terraform fmt -check`) must not gain a cloud dependency.
- `dbt-run.yml` (OIDC, Athena target) completes under the `run_key` completion contract with no
  interactive prompt and a loud, non-silent failure on timeout.

## dbt model changes (silver / gold / recon)

Apply when the diff touches `dbt/models/silver/**`, `dbt/models/gold/**`, or `dbt/models/recon/**`.

### Default AC
- `dbt build` passes on **both** targets (`duckdb` local/CI, `athena` cloud).
- Silver stays exactly-once per `tid` after the change — merge predicate still respects
  `source_rank` (backfill outranks ws) and `first_seen_source` stays insert-only lineage.
- `recon_trades` still proves G3: `ws_only = 0`, and every `backfill_only` row falls inside a
  manifest-recorded gap.

## Envelope / watchlist / partition-helper changes

Apply when the diff touches `src/hyperlake/**` (envelope schema, watchlist loader, partition
helper) or `config/watchlist.yaml`.

### Default AC
- `dt` is still derived from exchange event `time`, never arrival/`ingested_at`.
- HIP-3 partition normalisation (`:` → `_`) still goes through the one shared helper — no new
  ad hoc normalisation call site.
- Watchlist stays config-driven — no market list hardcoded back into code.

## Dependency changes

Apply when the diff touches a dependency manifest/lockfile (`pyproject.toml`, `uv.lock`,
`requirements*.txt`).

### Default AC
- Dependency audit is clean — no high/critical CVEs (`profile.verify.security`).
- The lockfile is updated and committed (no drift between manifest and lock).
- A CVE-driven bump is folded into this PR, not split into a separate ticket.

## README / reproducibility-claim changes

Apply when the diff touches `README.md` or `docs/architecture/system-overview.md`.

### Default AC
- Any reproduction-time (G1: bootstrap → queryable in Athena) or cost (G4) claim in the diff is
  freshly measured in this PR, not carried over from memory or a prior run.

<!-- Add more classes as patterns emerge (e.g. Lambda backfill code once QNT-451 lands, if its
     risk profile turns out distinct from the envelope/watchlist group above). -->
