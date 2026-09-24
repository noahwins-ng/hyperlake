#!/usr/bin/env python3
"""Portfolio-hygiene guard (QNT-479, Phase 4 retro), run via `make portfolio-lint`.

Two of the three hygiene defects the Phase 4 "portfolio review" pass (2026-09-17) caught only
by eyeballing the diff, hours before the repo went public:
  - a stray ticket-id comment reintroduced into src/hyperlake or dbt/ (the scope refactor/
    cc2e8c1 already swept -- scripts/, tests/, infra/*.tf and .github/workflows/*.yml keep
    ticket-id comments deliberately, as operational history, see
    docs/retros/phase-4-presentation.md);
  - a real-looking AWS account id embedded in an ARN or a hyperlake-* bucket name.

The third (a bare negative dollar figure in the README's generated cost block) is already
pinned by tests/test_cost_report.py::test_render_readme_block_never_shows_a_negative_headline_
number -- part of `make test`/`make check` since the same portfolio-review pass, so it needs no
separate check here.

Offline: reads `git ls-files` for the tracked-file list, no AWS credentials, no network call.
Binary tracked files (dbt/fixtures/*.parquet, docs/img/*.png) fail the UTF-8 decode and are
silently skipped -- both checks are text-comment/string concerns, not a gap in practice.
"""

import re
import subprocess
from pathlib import Path

TICKET_ID_RE = re.compile(r"QNT-\d+")
TICKET_ID_SCOPE_PREFIXES = ("src/hyperlake/", "dbt/")

# AWS's own documented example/placeholder account id, used throughout AWS's docs and this
# repo's test fixtures -- any other 12-digit id in an ARN or hyperlake-* bucket name is real.
ALLOWED_ACCOUNT_IDS = frozenset({"123456789012"})
ARN_ACCOUNT_ID_RE = re.compile(r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):")
BUCKET_ACCOUNT_ID_RE = re.compile(r"hyperlake-(?:data|tfstate)-(\d{12})\b")


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, check=True, text=True
    ).stdout
    return [root / line for line in out.splitlines() if line]


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text().splitlines()
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return []


def ticket_id_violations(files: list[Path], root: Path) -> list[str]:
    violations = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        if not rel.startswith(TICKET_ID_SCOPE_PREFIXES):
            continue
        for lineno, line in enumerate(_lines(f), start=1):
            if TICKET_ID_RE.search(line):
                violations.append(f"{rel}:{lineno}: {line.strip()}")
    return violations


def account_id_violations(files: list[Path], root: Path) -> list[str]:
    violations = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        for lineno, line in enumerate(_lines(f), start=1):
            matches = (*ARN_ACCOUNT_ID_RE.finditer(line), *BUCKET_ACCOUNT_ID_RE.finditer(line))
            ids = (m.group(1) for m in matches)
            if any(account_id not in ALLOWED_ACCOUNT_IDS for account_id in ids):
                violations.append(f"{rel}:{lineno}: {line.strip()}")
    return violations


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    files = tracked_files(root)

    problems = {
        "stray ticket-id comment (src/hyperlake, dbt/)": ticket_id_violations(files, root),
        "real-looking AWS account id": account_id_violations(files, root),
    }

    failed = False
    for label, violations in problems.items():
        for v in violations:
            print(f"{label}: {v}")
            failed = True

    if failed:
        raise SystemExit(1)

    print(f"portfolio-lint: {len(files)} tracked file(s) checked, 0 problems")


if __name__ == "__main__":
    main()
