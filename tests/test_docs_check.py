"""Pins the doc link checker (QNT-467 AC3): relative markdown links must resolve to a real
file on disk; external/anchor-only links are skipped (no network call in CI).
"""

from pathlib import Path

from scripts.docs_check import broken_links, check_files, extract_links


def test_extract_links_finds_link_and_image_targets():
    text = "See [the plan](docs/project-plan.md) and ![diagram](docs/diagram.png)."
    assert extract_links(text) == ["docs/project-plan.md", "docs/diagram.png"]


def test_broken_links_skips_external_and_anchor_only(tmp_path):
    source = tmp_path / "README.md"
    text = "[external](https://example.com/x) [mail](mailto:a@b.com) [anchor](#section)"
    assert broken_links(source, text) == []


def test_broken_links_flags_missing_relative_target(tmp_path):
    source = tmp_path / "README.md"
    text = "[missing](docs/nope.md)"
    assert broken_links(source, text) == ["docs/nope.md"]


def test_broken_links_passes_existing_relative_target(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "real.md").write_text("hi")
    source = tmp_path / "README.md"
    text = "[real](docs/real.md)"
    assert broken_links(source, text) == []


def test_broken_links_resolves_target_with_anchor_suffix(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "real.md").write_text("hi")
    source = tmp_path / "README.md"
    text = "[real section](docs/real.md#some-heading)"
    assert broken_links(source, text) == []


def test_broken_links_resolves_leading_slash_against_repo_root_not_filesystem_root(tmp_path):
    # A `](/foo)` link is repo-root-relative in the usual doc-site convention, not an
    # absolute filesystem path -- without `root`, it can't be resolved, so it's skipped
    # (not flagged) rather than checked against the wrong base.
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "real.md").write_text("hi")
    nested = tmp_path / "docs" / "nested.md"
    text = "[real](/docs/real.md)"

    assert broken_links(nested, text) == []
    assert broken_links(nested, text, root=tmp_path) == []
    assert broken_links(nested, "[missing](/docs/nope.md)", root=tmp_path) == ["/docs/nope.md"]


def test_check_files_only_reports_files_with_broken_links(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("[ok](good.md)")
    bad = tmp_path / "bad.md"
    bad.write_text("[gone](missing.md)")

    results = check_files([good, bad])

    assert list(results.keys()) == [bad]
    assert results[bad] == ["missing.md"]


def test_repo_readme_and_docs_have_no_broken_links():
    # End-to-end: the real README + docs tree, same file set `make docs-check` covers.
    root = Path(__file__).resolve().parent.parent
    files = [root / "README.md"] + sorted((root / "docs").rglob("*.md"))
    results = check_files(files)
    assert results == {}
