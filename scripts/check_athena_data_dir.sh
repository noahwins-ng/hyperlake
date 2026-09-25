#!/usr/bin/env bash
# Refuse to run dbt against Athena unless table data has its own location. Unset, dbt-athena
# writes tables under the staging dir, and athena-results/ expires after 7 days
# (infra/main/persistent/data_bucket.tf).
set -euo pipefail
case "${DBT_ATHENA_S3_DATA_DIR:-}" in
  "")
    echo "::error::DBT_ATHENA_S3_DATA_DIR is not set (repo variable); refusing to write tables under the staging dir"
    exit 1 ;;
  */athena-results/*|*/athena-results)
    echo "::error::DBT_ATHENA_S3_DATA_DIR ($DBT_ATHENA_S3_DATA_DIR) is under athena-results/, which expires after 7 days"
    exit 1 ;;
esac
echo "table data dir: $DBT_ATHENA_S3_DATA_DIR"
