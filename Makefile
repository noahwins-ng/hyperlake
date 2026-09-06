.PHONY: check lint format types test audit tf-check dbt-build cost-backfill tf-apply-persistent tf-destroy-persistent

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

tf-check:
	@if find . -name '*.tf' -not -path './.terraform/*' | grep -q .; then \
		terraform fmt -check -recursive .; \
		for dir in $$(find . -name '*.tf' -not -path './.terraform/*' -exec dirname {} \; | sort -u); do \
			(cd $$dir && terraform init -backend=false -input=false >/dev/null && terraform validate) || exit 1; \
		done; \
	else \
		echo "tf-check: no *.tf files yet — skipping"; \
	fi
