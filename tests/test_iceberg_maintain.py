from unittest.mock import MagicMock

import pytest

import scripts.iceberg_maintain as iceberg_maintain
from scripts.iceberg_maintain import iceberg_tables, maintain, run_query


class _EntityNotFoundException(Exception):
    pass


def _mock_glue(tables_by_database: dict[str, list[dict]], missing: set[str] = set()) -> MagicMock:
    glue = MagicMock()
    glue.exceptions.EntityNotFoundException = _EntityNotFoundException

    def get_paginator(op_name):
        assert op_name == "get_tables"
        paginator = MagicMock()

        def paginate(DatabaseName):
            if DatabaseName in missing:
                raise _EntityNotFoundException()
            return [{"TableList": tables_by_database.get(DatabaseName, [])}]

        paginator.paginate.side_effect = paginate
        return paginator

    glue.get_paginator.side_effect = get_paginator
    return glue


def test_iceberg_tables_filters_to_iceberg_type():
    glue = _mock_glue(
        {
            "silver": [
                {"Name": "trades", "Parameters": {"table_type": "ICEBERG"}},
                {"Name": "not_iceberg", "Parameters": {}},
            ]
        }
    )
    assert iceberg_tables(glue, "silver") == ["trades"]


def test_iceberg_tables_returns_empty_when_database_missing():
    glue = _mock_glue({}, missing={"gold"})
    assert iceberg_tables(glue, "gold") == []


def test_run_query_polls_until_succeeded():
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.side_effect = [
        {"QueryExecution": {"Status": {"State": "RUNNING"}}},
        {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}},
    ]

    run_query(athena, "silver", "OPTIMIZE silver.trades REWRITE DATA USING BIN_PACK")

    athena.start_query_execution.assert_called_once_with(
        QueryString="OPTIMIZE silver.trades REWRITE DATA USING BIN_PACK",
        QueryExecutionContext={"Database": "silver"},
        WorkGroup="hyperlake",
    )
    assert athena.get_query_execution.call_count == 2


def test_run_query_raises_on_timeout(monkeypatch):
    monkeypatch.setattr(iceberg_maintain, "QUERY_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(iceberg_maintain, "POLL_INTERVAL_SECONDS", 1)
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "RUNNING"}}}

    with pytest.raises(RuntimeError, match="timed out after 2s"):
        run_query(athena, "silver", "VACUUM silver.trades")


def test_run_query_raises_on_failure():
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "boom"}}
    }

    with pytest.raises(RuntimeError, match="boom"):
        run_query(athena, "silver", "VACUUM silver.trades")


def test_maintain_runs_optimize_and_vacuum_per_table_and_skips_missing_gold():
    glue = _mock_glue(
        {"silver": [{"Name": "trades", "Parameters": {"table_type": "ICEBERG"}}]},
        missing={"gold"},
    )
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}

    touched = maintain(athena, glue)

    assert touched == ["silver.trades"]
    queries = [c.kwargs["QueryString"] for c in athena.start_query_execution.call_args_list]
    assert queries == [
        "OPTIMIZE silver.trades REWRITE DATA USING BIN_PACK",
        "VACUUM silver.trades",
    ]
