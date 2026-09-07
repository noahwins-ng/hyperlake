.PHONY: check lint format types test audit tf-check dbt-build cost-backfill tf-apply-persistent tf-destroy-persistent build-backfill-lambda tf-apply-ephemeral tf-destroy-ephemeral backfill-hour backfill dbt-run iceberg-maintain tf-drift-check

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

tf-check:
	@if find . -name '*.tf' -not -path './.terraform/*' | grep -q .; then \
		terraform fmt -check -recursive .; \
		for dir in $$(find . -name '*.tf' -not -path './.terraform/*' -exec dirname {} \; | sort -u); do \
			(cd $$dir && terraform init -backend=false -input=false >/dev/null && terraform validate) || exit 1; \
		done; \
	else \
		echo "tf-check: no *.tf files yet — skipping"; \
	fi
