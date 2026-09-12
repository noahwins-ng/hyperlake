#!/usr/bin/env python3
"""Markdown link checker for README.md + docs/ (QNT-467 AC3), run via `make docs-check`.
Relative links must resolve to a real file on disk; external (http(s)/mailto) and
anchor-only links are skipped -- no network call, so it stays offline.
"""

import re
from pathlib import Path

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def extract_links(text: str) -> list[str]:
    return LINK_RE.findall(text)


def _is_external(link: str) -> bool:
    return link.startswith(("http://", "https://", "mailto:"))


def _is_anchor_only(link: str) -> bool:
    return link.startswith("#")


def broken_links(source_file: Path, text: str, root: Path | None = None) -> list[str]:
    broken = []
    for link in extract_links(text):
        link = link.strip()
        if not link or _is_external(link) or _is_anchor_only(link):
            continue
        target = link.split("#", 1)[0]
        if target.startswith("/"):
            # Repo-root-relative (the usual doc-site convention), not a filesystem
            # absolute path. Unresolvable without `root` -- skip rather than check
            # against the wrong base.
            if root is None:
                continue
            resolved = (root / target.lstrip("/")).resolve()
        else:
            resolved = (source_file.parent / target).resolve()
        if not resolved.exists():
            broken.append(link)
    return broken


def check_files(files: list[Path], root: Path | None = None) -> dict[Path, list[str]]:
    results = {}
    for f in files:
        b = broken_links(f, f.read_text(), root=root)
        if b:
            results[f] = b
    return results


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    files = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    results = check_files(files, root=root)
    total_links = sum(len(extract_links(f.read_text())) for f in files)

    if results:
        for f, links in results.items():
            for link in links:
                print(f"{f.relative_to(root)}: broken link -> {link}")
        raise SystemExit(1)

    print(f"{len(files)} file(s), {total_links} link(s) checked, 0 broken")


if __name__ == "__main__":
    main()
