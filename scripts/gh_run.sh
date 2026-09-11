#!/usr/bin/env bash
# Shared workflow-completion-contract helper (PRD FR-8 / ADR-001): dispatch a GitHub Actions
# workflow_dispatch run tagged with a caller-supplied run_key, locate that exact run by key
# (never "latest" -- two dispatches can land seconds apart), poll it to completion within a
# bounded timeout, and exit non-zero -- always printing the run URL -- on locate-timeout,
# run-timeout, or run failure. Polls `gh run view` itself rather than `gh run watch` so the
# timeout is enforced here (and overridable via GH_RUN_TIMEOUT_SECONDS for tests) instead of
# depending on an external `timeout` binary.
#
# Usage: scripts/gh_run.sh <run_key> [-f key=value ...]
set -euo pipefail

WORKFLOW="${GH_RUN_WORKFLOW:-dbt-run.yml}"
LOCATE_TIMEOUT_SECONDS="${GH_RUN_LOCATE_TIMEOUT_SECONDS:-60}"
TIMEOUT_SECONDS="${GH_RUN_TIMEOUT_SECONDS:-1200}" # 20 min, per FR-8
POLL_INTERVAL_SECONDS="${GH_RUN_POLL_INTERVAL_SECONDS:-5}"

run_key="${1:?usage: gh_run.sh <run_key> [-f key=value ...]}"
shift

# run_key is interpolated into a jq string literal below -- reject anything that could break
# out of it (or, for a future caller, be handed less-controlled input) rather than corrupt the
# query or silently locate the wrong run.
case "$run_key" in
*[!A-Za-z0-9._-]*)
  echo "gh_run.sh: run_key must match [A-Za-z0-9._-]+, got: $run_key" >&2
  exit 1
  ;;
esac

# QNT-466 (2026-09-11 live session): without --ref, `gh workflow run` dispatches against
# the repo's default branch, not the branch actually calling this script -- silently
# wrong for anything the workflow reads from the checkout (e.g. regen_recon_seed.py's
# sessions/<id>.json, which exists only on the caller's branch until the PR merges).
ref="$(git rev-parse --abbrev-ref HEAD)"
gh workflow run "$WORKFLOW" --ref "$ref" -f "run_key=$run_key" "$@"

run_id=""
elapsed=0
while [ "$elapsed" -lt "$LOCATE_TIMEOUT_SECONDS" ]; do
  run_id=$(gh run list --workflow "$WORKFLOW" --json databaseId,displayTitle \
    --jq ".[] | select(.displayTitle == \"$run_key\") | .databaseId" | head -n1)
  [ -n "$run_id" ] && break
  sleep "$POLL_INTERVAL_SECONDS"
  elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
done

if [ -z "$run_id" ]; then
  echo "gh_run.sh: timed out after ${LOCATE_TIMEOUT_SECONDS}s locating a run for run_key=$run_key" >&2
  exit 1
fi

run_url=$(gh run view "$run_id" --json url --jq .url)
echo "gh_run.sh: watching $run_url"

elapsed=0
status=""
conclusion=""
while [ "$elapsed" -lt "$TIMEOUT_SECONDS" ]; do
  read -r status conclusion <<<"$(gh run view "$run_id" --json status,conclusion --jq '"\(.status) \(.conclusion)"')"
  [ "$status" = "completed" ] && break
  sleep "$POLL_INTERVAL_SECONDS"
  elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
done

if [ "$status" != "completed" ]; then
  echo "gh_run.sh: timed out after ${TIMEOUT_SECONDS}s waiting for $run_url" >&2
  exit 1
fi

if [ "$conclusion" != "success" ]; then
  echo "gh_run.sh: run failed (conclusion=$conclusion): $run_url" >&2
  exit 1
fi

echo "gh_run.sh: run succeeded: $run_url"
