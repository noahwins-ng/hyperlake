#!/usr/bin/env bash
# Builds the backfill Lambda's deployment zip. No Docker needed: uv resolves manylinux
# wheels for the target platform without executing them, so this runs on a plain dev
# machine. Called both directly (`make build-backfill-lambda`) and by Terraform's
# `data "external"` block in backfill_lambda.tf (`--json`, QNT-451) -- that block only
# runs at plan/apply, never at `terraform validate`, so it adds no network dependency
# to the offline `make tf-check` gate.
set -euo pipefail

JSON_OUT=false
[ "${1:-}" = "--json" ] && JSON_OUT=true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/infra/main/ephemeral/build"
PKG_DIR="$BUILD_DIR/package"
ZIP_PATH="$BUILD_DIR/backfill_lambda.zip"

log() { if [ "$JSON_OUT" = true ]; then echo "$@" >&2; else echo "$@"; fi; }

rm -rf "$PKG_DIR" "$ZIP_PATH"
mkdir -p "$PKG_DIR"

# lz4, pyarrow, pyyaml (watchlist loader) are the reader's non-runtime deps -- boto3
# ships with the Lambda runtime already, so it's left out to keep the zip small.
# Pinned to uv.lock's resolved versions so the Lambda can never silently drift from
# what `make check` actually tests.
DEPS=$(uv export --no-emit-project --format requirements.txt --quiet 2>/dev/null \
  | grep -E '^(lz4|pyarrow|pyyaml)==' | sed 's/ \\$//')
# DEPS is a deliberate word-split arg list (one uv pip install arg per package).
# shellcheck disable=SC2086
uv pip install \
  --target "$PKG_DIR" \
  --python-platform x86_64-unknown-linux-gnu \
  --python-version 3.12 \
  --only-binary=:all: \
  $DEPS >&2

cp -r "$ROOT/src/hyperlake" "$PKG_DIR/hyperlake"
find "$PKG_DIR/hyperlake" -name '__pycache__' -type d -exec rm -rf {} +

# hyperlake.watchlist's default path resolves against a src-layout checkout, which
# doesn't exist inside the zip -- bundle the config alongside the package instead;
# the handler passes this path explicitly (LAMBDA_TASK_ROOT).
mkdir -p "$PKG_DIR/config"
cp "$ROOT/config/watchlist.yaml" "$PKG_DIR/config/watchlist.yaml"

(cd "$PKG_DIR" && zip -qr "$ZIP_PATH" .)
log "built $ZIP_PATH ($(du -h "$ZIP_PATH" | cut -f1))"

if [ "$JSON_OUT" = true ]; then
  sha256_base64=$(openssl dgst -sha256 -binary "$ZIP_PATH" | openssl base64)
  printf '{"path": "%s", "sha256_base64": "%s"}\n' "$ZIP_PATH" "$sha256_base64"
fi
