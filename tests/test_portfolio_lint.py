"""Pins the portfolio-hygiene guard (QNT-479, Phase 4 retro): stray ticket-id comments in
src/hyperlake or dbt/, and a real-looking AWS account id in an ARN or hyperlake-* bucket name,
must both fail loud. AC3 (the README cost block never showing a bare negative headline figure)
is already pinned by test_cost_report.py's test_render_readme_block_never_shows_a_negative_
headline_number -- no new check needed for it here.
"""

from pathlib import Path

from scripts.portfolio_lint import account_id_violations, ticket_id_violations


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_ticket_id_violations_flags_src_hyperlake(tmp_path):
    f = _write(tmp_path, "src/hyperlake/heal.py", "# QNT-461: heal unhealed gaps\n")

    violations = ticket_id_violations([f], tmp_path)

    assert violations == ["src/hyperlake/heal.py:1: # QNT-461: heal unhealed gaps"]


def test_ticket_id_violations_flags_dbt(tmp_path):
    f = _write(tmp_path, "dbt/models/silver/trades.sql", "-- QNT-462 seam test\n")

    violations = ticket_id_violations([f], tmp_path)

    assert violations == ["dbt/models/silver/trades.sql:1: -- QNT-462 seam test"]


def test_ticket_id_violations_ignores_scripts_and_workflows(tmp_path):
    files = [
        _write(tmp_path, "scripts/heal.py", "# QNT-461: called by make heal\n"),
        _write(tmp_path, ".github/workflows/ci.yml", "# QNT-470: dbt docs\n"),
        _write(tmp_path, "tests/test_heal.py", "# QNT-461 regression\n"),
        _write(tmp_path, "docs/project-plan.md", "- [x] QNT-461: make heal\n"),
    ]

    assert ticket_id_violations(files, tmp_path) == []


def test_ticket_id_violations_ignores_clean_files(tmp_path):
    f = _write(tmp_path, "src/hyperlake/heal.py", "# ADR-003: heal unhealed gaps\n")

    assert ticket_id_violations([f], tmp_path) == []


# Fixture ids built from parts, like ci.yml's "No long-lived AWS credentials" check, so this
# test file's own source never contains a contiguous ARN/bucket-shaped id that portfolio-lint
# would flag when it scans the repo's own tracked files.
_FAKE_ID = "999988" + "887777"


def test_account_id_violations_flags_arn_with_real_looking_id(tmp_path):
    f = _write(
        tmp_path,
        "tests/test_audit_teardown.py",
        f'KINESIS_ARN = "arn:aws:kinesis:ap-northeast-1:{_FAKE_ID}:stream/hyperlake-trades"\n',
    )

    violations = account_id_violations([f], tmp_path)

    assert violations == [
        "tests/test_audit_teardown.py:1: "
        f'KINESIS_ARN = "arn:aws:kinesis:ap-northeast-1:{_FAKE_ID}:stream/hyperlake-trades"'
    ]


def test_account_id_violations_flags_bucket_name_with_real_looking_id(tmp_path):
    f = _write(
        tmp_path,
        "tests/test_session_reaper.py",
        f'S3_ARN = "arn:aws:s3:::hyperlake-data-{_FAKE_ID}"\n',
    )

    assert len(account_id_violations([f], tmp_path)) == 1


def test_account_id_violations_allows_the_documented_placeholder(tmp_path):
    f = _write(
        tmp_path,
        "tests/test_audit_teardown.py",
        'KINESIS_ARN = "arn:aws:kinesis:ap-northeast-1:123456789012:stream/hyperlake-trades"\n'
        'S3_ARN = "arn:aws:s3:::hyperlake-data-123456789012"\n',
    )

    assert account_id_violations([f], tmp_path) == []


def test_account_id_violations_ignores_unrelated_12_digit_numbers(tmp_path):
    f = _write(
        tmp_path,
        "sessions/qnt-466-20260911124320.json",
        '{"tid": 105039561233, "session_id": "202609111243"}\n',
    )

    assert account_id_violations([f], tmp_path) == []
