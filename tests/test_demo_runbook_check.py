"""Pins the demo runbook command checker (QNT-468 AC2): every `make <target>` the runbook
tells a reader to run must be a real Makefile target, and every referenced
`docs/queries/*.sql` file must exist on disk.
"""

from pathlib import Path

from scripts.demo_runbook_check import (
    extract_make_targets,
    extract_query_files,
    makefile_targets,
    missing_commands,
)


def test_extract_make_targets_finds_target_names_ignoring_args():
    text = "Run `make session-up LABEL=qnt-468` then `make recon SESSION=$ID`."
    assert extract_make_targets(text) == {"session-up", "recon"}


def test_extract_query_files_finds_docs_queries_sql_paths():
    text = "See [bronze](docs/queries/bronze.sql) and docs/queries/gold.sql."
    assert extract_query_files(text) == {"docs/queries/bronze.sql", "docs/queries/gold.sql"}


def test_makefile_targets_ignores_recipe_lines_and_phony():
    makefile = ".PHONY: check lint\ncheck: lint\n\nlint:\n\tuv run ruff check .\n"
    assert makefile_targets(makefile) == {"check", "lint"}


def test_missing_commands_flags_unknown_target_and_missing_query_file(tmp_path):
    (tmp_path / "docs" / "queries").mkdir(parents=True)
    (tmp_path / "docs" / "queries" / "bronze.sql").write_text("select 1;")
    runbook_text = (
        "`make session-up` and `make nope`; see docs/queries/bronze.sql and docs/queries/nope.sql"
    )
    makefile_text = "session-up:\n\techo up\n"

    missing = missing_commands(runbook_text, makefile_text, tmp_path)

    assert missing == ["make nope", "docs/queries/nope.sql"]


def test_missing_commands_passes_when_everything_resolves(tmp_path):
    (tmp_path / "docs" / "queries").mkdir(parents=True)
    (tmp_path / "docs" / "queries" / "bronze.sql").write_text("select 1;")
    runbook_text = "`make session-up`; see docs/queries/bronze.sql"
    makefile_text = "session-up:\n\techo up\n"

    assert missing_commands(runbook_text, makefile_text, tmp_path) == []


def test_repo_demo_runbook_has_no_missing_commands():
    root = Path(__file__).resolve().parent.parent
    runbook_text = (root / "docs" / "demo-runbook.md").read_text()
    makefile_text = (root / "Makefile").read_text()
    assert missing_commands(runbook_text, makefile_text, root) == []
