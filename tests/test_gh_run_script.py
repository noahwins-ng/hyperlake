"""Tests scripts/gh_run.sh (QNT-454 AC3, code) against a stubbed `gh` on PATH -- the real
`gh` CLI is exercised separately as AC1/AC2's dev-execution ACs. The stub is a tiny bash
dispatcher keyed on argv[0:2]; each case prints exactly what real `gh ... --jq <expr>` would
(raw, unquoted -- confirmed against the real CLI), so `gh_run.sh` doesn't know it's talking to
a stub. Env vars shrink the 20-minute default timeout/poll interval to something a test can
actually wait out.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gh_run.sh"

STUB_PREAMBLE = """#!/usr/bin/env bash
set -euo pipefail
case "$1 $2" in
"""

STUB_POSTAMBLE = """
*)
  echo "unhandled stub gh invocation: $*" >&2
  exit 1
  ;;
esac
"""


def _run(tmp_path: Path, stub_body: str, *args: str, env: dict | None = None):
    gh_stub = tmp_path / "gh"
    gh_stub.write_text(STUB_PREAMBLE + stub_body + STUB_POSTAMBLE)
    gh_stub.chmod(0o755)

    full_env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    full_env.update(
        {
            "GH_RUN_TIMEOUT_SECONDS": "2",
            "GH_RUN_LOCATE_TIMEOUT_SECONDS": "2",
            "GH_RUN_POLL_INTERVAL_SECONDS": "1",
        }
    )
    if env:
        full_env.update(env)

    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=15,
    )


# `gh run view ... --json url --jq .url` prints the raw URL; `... --json status,conclusion
# --jq '"\\(.status) \\(.conclusion)"'` prints "<status> <conclusion>" raw. Distinguish which
# was asked for by grepping "$*" for "--json url".
_RUN_VIEW_CASE = """
"run view")
  if [[ "$*" == *"--json url"* ]]; then
    echo "https://github.com/x/y/actions/runs/$3"
  else
    echo "$GH_STUB_STATUS $GH_STUB_CONCLUSION"
  fi
  ;;
"""


def test_succeeds_when_run_completes_successfully(tmp_path):
    stub = (
        """
"workflow run") exit 0 ;;
"run list") echo 123 ;;
"""
        + _RUN_VIEW_CASE
    )
    result = _run(
        tmp_path,
        stub,
        "mykey",
        env={"GH_STUB_STATUS": "completed", "GH_STUB_CONCLUSION": "success"},
    )

    assert result.returncode == 0, result.stderr
    assert "run succeeded" in result.stdout
    assert "https://github.com/x/y/actions/runs/123" in result.stdout


def test_exits_nonzero_and_prints_url_when_run_fails(tmp_path):
    stub = (
        """
"workflow run") exit 0 ;;
"run list") echo 123 ;;
"""
        + _RUN_VIEW_CASE
    )
    result = _run(
        tmp_path,
        stub,
        "mykey",
        env={"GH_STUB_STATUS": "completed", "GH_STUB_CONCLUSION": "failure"},
    )

    assert result.returncode != 0
    assert "run failed" in result.stderr
    assert "https://github.com/x/y/actions/runs/123" in result.stderr


def test_rejects_run_key_with_unsafe_characters(tmp_path):
    # run_key is interpolated into a jq string literal -- a `"` would corrupt the query
    # (or worse, on a future less-trusted caller) rather than just fail to locate the run.
    stub = """
"workflow run") exit 0 ;;
"""
    result = _run(tmp_path, stub, 'mykey"; malicious')

    assert result.returncode != 0
    assert "run_key must match" in result.stderr


def test_enforces_timeout_when_run_never_completes(tmp_path):
    stub = (
        """
"workflow run") exit 0 ;;
"run list") echo 123 ;;
"""
        + _RUN_VIEW_CASE
    )
    result = _run(
        tmp_path, stub, "mykey", env={"GH_STUB_STATUS": "in_progress", "GH_STUB_CONCLUSION": "null"}
    )

    assert result.returncode != 0
    assert "timed out after 2s" in result.stderr
    assert "https://github.com/x/y/actions/runs/123" in result.stderr


def test_enforces_locate_timeout_when_no_run_matches_key(tmp_path):
    stub = """
"workflow run") exit 0 ;;
"run list") echo -n "" ;;
"""
    result = _run(tmp_path, stub, "mykey")

    assert result.returncode != 0
    assert "timed out after 2s locating a run for run_key=mykey" in result.stderr


def test_distinguishes_two_back_to_back_keys(tmp_path):
    # AC1's shape, exercised against the stub: two different run_keys resolve to two
    # different run URLs, never falling back to "latest".
    stub = (
        """
"workflow run") exit 0 ;;
"run list")
  case "$*" in
  *keyA*) echo 1 ;;
  *keyB*) echo 2 ;;
  esac
  ;;
"""
        + _RUN_VIEW_CASE
    )
    result_a = _run(
        tmp_path, stub, "keyA", env={"GH_STUB_STATUS": "completed", "GH_STUB_CONCLUSION": "success"}
    )
    result_b = _run(
        tmp_path, stub, "keyB", env={"GH_STUB_STATUS": "completed", "GH_STUB_CONCLUSION": "success"}
    )

    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    assert "runs/1" in result_a.stdout
    assert "runs/2" in result_b.stdout
