.PHONY: check lint format types test audit tf-check dbt-build dbt-demo-fail cost-backfill tf-apply-persistent tf-destroy-persistent build-backfill-lambda tf-apply-ephemeral tf-destroy-ephemeral backfill-hour backfill-fallback backfill dbt-run iceberg-maintain tf-drift-check ingester-start ingester-stop session-up session-down recon heal bronze-query

# Everything ci.yml runs, in order — the local sanity gate (workflow-profile.yaml verify.*).
check: lint format types test audit dbt-build tf-check

lint:
	uv run ruff check .

format:
	uv run ruff format --check .

types:
	uv run pyright

test:
	uv run pytest

# Known-vulnerability scan of the locked dependency set (profile verify.security). The lock is
# fully resolved, so --no-deps is exact, not a shortcut. requirements-audit.txt is gitignored.
audit:
	uv export --all-groups --no-emit-project --format requirements.txt --quiet -o requirements-audit.txt
	uv run pip-audit -r requirements-audit.txt --disable-pip --no-deps --progress-spinner off

# `--group dbt` makes `uv run` install the dbt group on the fly, so this needs no separate
# `uv sync --group dbt` step — relies on that uv auto-sync behavior.
dbt-build:
	cd dbt && uv run --group dbt dbt build --profiles-dir . --target duckdb

# QNT-464 AC2/G5: seeds one bad row (an invalid `side`) via a demo-only bronze fixture and
# runs `dbt build` so the run summary shows the red silver test and gold SKIPping downstream
# of it -- "failures are visible in the demo" (Phase 4).
dbt-demo-fail:
	cd dbt && uv run --group dbt dbt build --profiles-dir . --target duckdb --vars '{dbt_demo_fail: true}'

# Fills cost_actual_usd for pending costs/sessions.csv rows once Cost Explorer data is ready
# (costs/README.md). Needs AWS credentials; not part of `check`/CI.
cost-backfill:
	uv run python scripts/cost_backfill.py

# Persistent layer (S3 data bucket + Glue catalog + Athena workgroup) -- survives
# session teardown, so destroy is confirmation-guarded.
tf-apply-persistent:
	cd infra/main/persistent && terraform init -backend-config=../backend.hcl -input=false && terraform apply

tf-destroy-persistent:
	@printf '%s' "Destroy the persistent stack (data bucket + Glue catalog + Athena workgroup)? [y/N] "; \
	read ans; \
	[ "$$ans" = "y" ] || (echo "aborted"; exit 1)
	cd infra/main/persistent && terraform init -backend-config=../backend.hcl -input=false && terraform destroy

# QNT-451 backfill Lambda: build the zip, apply/destroy the ephemeral stack that runs it,
# and invoke it for one hour file.
build-backfill-lambda:
	./scripts/build_backfill_lambda.sh

tf-apply-ephemeral:
	cd infra/main/ephemeral && terraform init -backend-config=../backend.hcl -input=false && \
	terraform apply -var "tfstate_bucket=$$(terraform -chdir=../../bootstrap output -raw state_bucket)" $(TF_ARGS)

tf-destroy-ephemeral:
	cd infra/main/ephemeral && terraform init -backend-config=../backend.hcl -input=false && \
	terraform destroy -var "tfstate_bucket=$$(terraform -chdir=../../bootstrap output -raw state_bucket)" $(TF_ARGS)

backfill-hour:
	@test -n "$(DATE)" || (echo "usage: make backfill-hour DATE=YYYYMMDD HOUR=H"; exit 1)
	@test -n "$(HOUR)" || (echo "usage: make backfill-hour DATE=YYYYMMDD HOUR=H"; exit 1)
	aws lambda invoke --region ap-northeast-1 --function-name hyperlake-backfill-official \
	  --cli-binary-format raw-in-base64-out \
	  --payload '{"date":"$(DATE)","hour":"$(HOUR)"}' \
	  /tmp/backfill-hour-response.json && cat /tmp/backfill-hour-response.json

# QNT-465: Reservoir daily-file fallback for a date whose official hour files are still
# missing 24h after they should have landed (manual decision -- see the ops runbook).
# Runs locally (no Lambda) against HYPERLAKE_DATA_BUCKET.
backfill-fallback:
	@test -n "$(DATE)" || (echo "usage: make backfill-fallback DATE=YYYY-MM-DD"; exit 1)
	uv run python -m hyperlake.backfill.reservoir --date $(DATE)

# QNT-452: fan out the backfill Lambda over [FROM, TO] (inclusive, ISO dates) through
# the Step Functions state machine; waits for completion and reports failed hours.
backfill:
	@test -n "$(FROM)" || (echo "usage: make backfill FROM=YYYY-MM-DD TO=YYYY-MM-DD"; exit 1)
	@test -n "$(TO)" || (echo "usage: make backfill FROM=YYYY-MM-DD TO=YYYY-MM-DD"; exit 1)
	uv run python scripts/backfill.py --from $(FROM) --to $(TO) \
	  --state-machine-arn "$$(cd infra/main/ephemeral && terraform output -raw backfill_state_machine_arn)"

# QNT-454: dbt-run workflow via the shared run_key completion contract (scripts/gh_run.sh).
# ARGS forwards extra workflow_dispatch inputs, e.g. `make dbt-run ARGS="-f select=trades"`.
dbt-run:
	./scripts/gh_run.sh "dbt-run-$$(date +%s)-$$$$" $(ARGS)

# QNT-454: Athena OPTIMIZE + VACUUM on every Iceberg table (silver, and gold once it exists).
iceberg-maintain:
	uv run python scripts/iceberg_maintain.py

# QNT-474: compare infra/main/persistent's Terraform state against the live Glue catalog;
# fails loudly on drift in either direction (a live database/table Terraform doesn't know
# about, or a state entry with no live counterpart). Needs AWS credentials -- not part of
# `check`/CI, which stays offline; run from a dev session or the scheduled tf-drift-check.yml.
tf-drift-check:
	cd infra/main/persistent && terraform init -backend-config=../backend.hcl -input=false >/dev/null
	uv run python scripts/tf_drift_check.py

# QNT-457: the primitives session-up/session-down (own tickets) will call to bring the
# already-applied ECS service up/down, without a Terraform apply/destroy round trip.
ingester-start:
	aws ecs update-service --region ap-northeast-1 \
	  --cluster "$$(cd infra/main/ephemeral && terraform output -raw ecs_cluster_name)" \
	  --service "$$(cd infra/main/ephemeral && terraform output -raw ecs_service_name)" \
	  --desired-count 1 >/dev/null
	@echo "ingester-start: desired count 1"

ingester-stop:
	aws ecs update-service --region ap-northeast-1 \
	  --cluster "$$(cd infra/main/ephemeral && terraform output -raw ecs_cluster_name)" \
	  --service "$$(cd infra/main/ephemeral && terraform output -raw ecs_service_name)" \
	  --desired-count 0 >/dev/null
	@echo "ingester-stop: desired count 0"

# QNT-458: the scripted session lifecycle -- preflight guard, apply, ingester-start,
# manifest stub (session-up); stop, drain, destroy, cost_estimate, dbt-run,
# iceberg-maintain, finalize + commit (session-down). LABEL sets the session_id prefix,
# e.g. `make session-up LABEL=qnt-460`.
session-up:
	uv run python scripts/session_up.py --label $(or $(LABEL),dev)

session-down:
	uv run python scripts/session_down.py $(if $(DBT_ARGS),--dbt-args $(DBT_ARGS))

# QNT-460: G3 reconciliation (ADR-003) -- computes the session's reconcilable window,
# regenerates dbt/seeds/session_gaps.csv from its manifest gaps, runs recon_trades +
# its tests via dbt-run (`select tag:recon`), and writes the resulting
# ws_only/backfill_only/both counts into the manifest's recon block.
recon:
	@test -n "$(SESSION)" || (echo "usage: make recon SESSION=<session_id>"; exit 1)
	uv run python scripts/recon.py --session $(SESSION)

# QNT-461: expand every unhealed gap in the session's manifest to covering archive hour
# files (+ trailing hour), re-backfill them, re-run dbt-run with a lookback sized to the
# oldest unhealed gap, re-run recon, flip healed: true, commit. Idempotent -- a session
# with nothing unhealed no-ops before touching AWS.
heal:
	@test -n "$(SESSION)" || (echo "usage: make heal SESSION=<session_id>"; exit 1)
	uv run python scripts/heal.py --session $(SESSION)

# QNT-476: ad-hoc Athena query wrapper for bronze.trades_raw -- DT_FROM (required) bounds
# the partition-projection scan; DT_TO/SELECT/WHERE/LIMIT are optional passthroughs, e.g.
# `make bronze-query DT_FROM=2026-09-03 SELECT=tid,coin WHERE="coin = 'BTC'"`.
bronze-query:
	@test -n "$(DT_FROM)" || (echo "usage: make bronze-query DT_FROM=YYYY-MM-DD [DT_TO=YYYY-MM-DD] [SELECT=cols] [WHERE=clause] [LIMIT=n]"; exit 1)
	uv run python scripts/bronze_query.py --dt-from "$(DT_FROM)" \
	  $(if $(DT_TO),--dt-to "$(DT_TO)") \
	  $(if $(SELECT),--select "$(SELECT)") \
	  $(if $(WHERE),--where "$(WHERE)") \
	  $(if $(LIMIT),--limit "$(LIMIT)")

tf-check:
	@if find . -name '*.tf' -not -path './.terraform/*' | grep -q .; then \
		terraform fmt -check -recursive .; \
		for dir in $$(find . -name '*.tf' -not -path './.terraform/*' -exec dirname {} \; | sort -u); do \
			(cd $$dir && terraform init -backend=false -input=false >/dev/null && terraform validate) || exit 1; \
		done; \
	else \
		echo "tf-check: no *.tf files yet — skipping"; \
	fi
