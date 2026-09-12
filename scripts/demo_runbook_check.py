#!/usr/bin/env python3
"""Checks every command `docs/demo-runbook.md` tells a reader to run (QNT-468 AC2), run via
`make demo-runbook-check`. Each `make <target>` referenced must be a real Makefile target;
each referenced `docs/queries/*.sql` file must exist on disk. Offline (no network call).
"""

import re
from pathlib import Path

MAKE_TARGET_RE = re.compile(r"\bmake ([a-zA-Z0-9_-]+)")
QUERY_FILE_RE = re.compile(r"\bdocs/queries/[a-zA-Z0-9_./-]+\.sql\b")
MAKEFILE_TARGET_RE = re.compile(r"^([a-zA-Z0-9_-]+):")


def extract_make_targets(text: str) -> set[str]:
    return set(MAKE_TARGET_RE.findall(text))


def extract_query_files(text: str) -> set[str]:
    return set(QUERY_FILE_RE.findall(text))


def makefile_targets(makefile_text: str) -> set[str]:
    matches = (MAKEFILE_TARGET_RE.match(line) for line in makefile_text.splitlines())
    return {m.group(1) for m in matches if m}


def missing_commands(runbook_text: str, makefile_text: str, root: Path) -> list[str]:
    missing = []
    targets = makefile_targets(makefile_text)
    for target in sorted(extract_make_targets(runbook_text)):
        if target not in targets:
            missing.append(f"make {target}")
    for query_file in sorted(extract_query_files(runbook_text)):
        if not (root / query_file).exists():
            missing.append(query_file)
    return missing


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    runbook = root / "docs" / "demo-runbook.md"
    runbook_text = runbook.read_text()
    makefile_text = (root / "Makefile").read_text()

    missing = missing_commands(runbook_text, makefile_text, root)
    if missing:
        for item in missing:
            print(f"{runbook.relative_to(root)}: does not resolve -> {item}")
        raise SystemExit(1)

    n_targets = len(extract_make_targets(runbook_text))
    n_files = len(extract_query_files(runbook_text))
    print(f"{n_targets} make target(s), {n_files} query file(s) checked, 0 missing")


if __name__ == "__main__":
    main()
