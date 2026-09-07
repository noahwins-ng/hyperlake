import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from scripts.tf_drift_check import check, live_glue_resources, state_glue_resources, terraform_state


def _state(resources: list[dict]) -> dict:
    return {"resources": resources}


def _db_resource(name: str) -> dict:
    return {
        "type": "aws_glue_catalog_database",
        "name": name,
        "instances": [{"attributes": {"name": name}}],
    }


def _table_resource(name: str, database_name: str) -> dict:
    return {
        "type": "aws_glue_catalog_table",
        "name": name,
        "instances": [{"attributes": {"name": name, "database_name": database_name}}],
    }


def _mock_glue(
    databases: list[str],
    tables_by_database: dict[str, list[str]],
    denied: set[str] = set(),
) -> MagicMock:
    glue = MagicMock()

    def get_paginator(op_name):
        paginator = MagicMock()
        if op_name == "get_databases":
            paginator.paginate.return_value = [{"DatabaseList": [{"Name": d} for d in databases]}]
        elif op_name == "get_tables":

            def paginate(DatabaseName):
                if DatabaseName in denied:
                    raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetTables")
                tables = tables_by_database.get(DatabaseName, [])
                return [{"TableList": [{"Name": t} for t in tables]}]

            paginator.paginate.side_effect = paginate
        else:
            raise AssertionError(f"unexpected paginator op {op_name!r}")
        return paginator

    glue.get_paginator.side_effect = get_paginator
    return glue


def test_state_glue_resources_parses_databases_and_tables():
    state = _state(
        [
            _db_resource("bronze"),
            _db_resource("silver"),
            _table_resource("trades_raw", "bronze"),
        ]
    )
    databases, tables = state_glue_resources(state)
    assert databases == {"bronze", "silver"}
    assert tables == {("bronze", "trades_raw")}


def test_state_glue_resources_ignores_non_glue_resources():
    state = _state([{"type": "aws_s3_bucket", "name": "data", "instances": [{"attributes": {}}]}])
    assert state_glue_resources(state) == (set(), set())


def test_live_glue_resources_scopes_table_enumeration_to_tf_tracked_databases():
    # "silver" is live and has a table (dbt-athena's territory, per CLAUDE.md), but
    # Terraform tracks no tables there -- only "bronze" (tf_tables below) gets enumerated.
    glue = _mock_glue(
        databases=["bronze", "silver"],
        tables_by_database={"bronze": ["trades_raw"], "silver": ["trades"]},
    )
    databases, tables = live_glue_resources(glue, tf_tables={("bronze", "trades_raw")})
    assert databases == {"bronze", "silver"}
    assert tables == {("bronze", "trades_raw")}


def test_live_glue_resources_excludes_ignored_databases():
    glue = _mock_glue(databases=["bronze", "default"], tables_by_database={})
    databases, _ = live_glue_resources(glue, tf_tables=set())
    assert databases == {"bronze"}


def test_live_glue_resources_skips_database_it_cannot_inspect():
    glue = _mock_glue(
        databases=["bronze"],
        tables_by_database={"bronze": ["trades_raw"]},
        denied={"bronze"},
    )
    databases, tables = live_glue_resources(glue, tf_tables={("bronze", "trades_raw")})
    assert databases == {"bronze"}
    assert tables == set()


def test_live_glue_resources_propagates_unexpected_glue_errors():
    # Only EntityNotFoundException/AccessDeniedException are treated as skippable -- anything
    # else (throttling, internal errors, ...) must fail loudly rather than read as "no drift".
    def get_paginator(op_name):
        paginator = MagicMock()
        if op_name == "get_databases":
            paginator.paginate.return_value = [{"DatabaseList": [{"Name": "bronze"}]}]
        elif op_name == "get_tables":

            def paginate(DatabaseName):
                raise ClientError({"Error": {"Code": "ThrottlingException"}}, "GetTables")

            paginator.paginate.side_effect = paginate
        return paginator

    glue = MagicMock()
    glue.get_paginator.side_effect = get_paginator
    with pytest.raises(ClientError):
        live_glue_resources(glue, tf_tables={("bronze", "trades_raw")})


def test_check_reports_hand_created_database():
    state = _state([_db_resource("bronze")])
    glue = _mock_glue(databases=["bronze", "rogue"], tables_by_database={})
    findings = check(state, glue)
    assert findings["hand_created_databases"] == ["rogue"]
    assert findings["orphan_state_databases"] == []
    assert findings["hand_created_tables"] == []
    assert findings["orphan_state_tables"] == []


def test_check_reports_orphan_state_database():
    state = _state([_db_resource("bronze"), _db_resource("ghost")])
    glue = _mock_glue(databases=["bronze"], tables_by_database={})
    findings = check(state, glue)
    assert findings["orphan_state_databases"] == ["ghost"]
    assert findings["hand_created_databases"] == []


def test_check_reports_hand_created_and_orphan_tables():
    state = _state([_db_resource("bronze"), _table_resource("trades_raw", "bronze")])
    glue = _mock_glue(databases=["bronze"], tables_by_database={"bronze": ["evil_table"]})
    findings = check(state, glue)
    assert findings["hand_created_tables"] == ["bronze.evil_table"]
    assert findings["orphan_state_tables"] == ["bronze.trades_raw"]


def test_check_does_not_flag_dbt_owned_tables_or_the_aws_default_database():
    # Regression: silver.trades is a real dbt-athena-created Iceberg table (Terraform only
    # declares the "silver" database, never its tables), and "default" is Glue's
    # auto-created per-account database -- neither is drift.
    state = _state(
        [_db_resource("bronze"), _db_resource("silver"), _table_resource("trades_raw", "bronze")]
    )
    glue = _mock_glue(
        databases=["bronze", "silver", "default"],
        tables_by_database={"bronze": ["trades_raw"], "silver": ["trades"]},
    )
    findings = check(state, glue)
    assert findings == {
        "hand_created_databases": [],
        "orphan_state_databases": [],
        "hand_created_tables": [],
        "orphan_state_tables": [],
    }


def test_check_reports_no_drift_when_state_matches_live():
    state = _state([_db_resource("bronze"), _table_resource("trades_raw", "bronze")])
    glue = _mock_glue(databases=["bronze"], tables_by_database={"bronze": ["trades_raw"]})
    findings = check(state, glue)
    assert findings == {
        "hand_created_databases": [],
        "orphan_state_databases": [],
        "hand_created_tables": [],
        "orphan_state_tables": [],
    }


def test_terraform_state_parses_stdout_json(monkeypatch):
    import scripts.tf_drift_check as tf_drift_check

    def fake_run(cmd, cwd, capture_output, text, timeout):
        assert cmd == ["terraform", "state", "pull"]
        assert cwd == "infra/main/persistent"
        assert timeout == tf_drift_check.STATE_PULL_TIMEOUT_SECONDS
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"resources": []})
        result.stderr = ""
        return result

    monkeypatch.setattr(tf_drift_check.subprocess, "run", fake_run)
    assert terraform_state("infra/main/persistent") == {"resources": []}


def test_terraform_state_raises_on_nonzero_exit(monkeypatch):
    import scripts.tf_drift_check as tf_drift_check

    def fake_run(cmd, cwd, capture_output, text, timeout):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "no backend configured"
        return result

    monkeypatch.setattr(tf_drift_check.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="no backend configured"):
        terraform_state("infra/main/persistent")
