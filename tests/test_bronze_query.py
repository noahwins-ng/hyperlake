from unittest.mock import MagicMock

import pytest

import scripts.bronze_query as bronze_query
from scripts.bronze_query import build_query, parse_args, run_query


def test_dt_from_is_required_and_no_athena_call_is_made(monkeypatch):
    # AC1: no --dt-from exits non-zero via argparse, before boto3.client is ever touched.
    client_calls = []
    monkeypatch.setattr(bronze_query.boto3, "client", lambda *a, **kw: client_calls.append(1))

    with pytest.raises(SystemExit) as exc_info:
        bronze_query.main([])

    assert exc_info.value.code != 0
    assert client_calls == []


def test_parse_args_requires_dt_from():
    with pytest.raises(SystemExit):
        parse_args(["--select", "tid"])


def test_parse_args_accepts_dt_from_alone():
    args = parse_args(["--dt-from", "2026-09-03"])
    assert args.dt_from == "2026-09-03"
    assert args.dt_to is None


def test_build_query_defaults_dt_to_to_dt_from():
    sql = build_query("2026-09-03", "2026-09-03", "*", None, 100)
    assert sql == (
        "SELECT * FROM bronze.trades_raw "
        "WHERE dt >= DATE '2026-09-03' AND dt <= DATE '2026-09-03' LIMIT 100"
    )


def test_build_query_includes_extra_where_anded_with_dt_bound():
    sql = build_query("2026-09-03", "2026-09-04", "tid, coin", "coin = 'BTC'", 10)
    assert sql == (
        "SELECT tid, coin FROM bronze.trades_raw "
        "WHERE dt >= DATE '2026-09-03' AND dt <= DATE '2026-09-04' AND (coin = 'BTC') LIMIT 10"
    )


def test_run_query_polls_until_succeeded_and_parses_rows():
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.side_effect = [
        {"QueryExecution": {"Status": {"State": "RUNNING"}}},
        {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}},
    ]
    athena.get_query_results.return_value = {
        "ResultSet": {
            "Rows": [
                {"Data": [{"VarCharValue": "tid"}, {"VarCharValue": "coin"}]},
                {"Data": [{"VarCharValue": "123"}, {"VarCharValue": "BTC"}]},
            ]
        }
    }

    rows = run_query(
        athena, "SELECT tid, coin FROM bronze.trades_raw WHERE dt >= DATE '2026-09-03'"
    )

    athena.start_query_execution.assert_called_once_with(
        QueryString="SELECT tid, coin FROM bronze.trades_raw WHERE dt >= DATE '2026-09-03'",
        QueryExecutionContext={"Database": "bronze"},
        WorkGroup="hyperlake",
    )
    assert rows == [{"tid": "123", "coin": "BTC"}]


def test_run_query_raises_on_failure():
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "boom"}}
    }

    with pytest.raises(RuntimeError, match="boom"):
        run_query(athena, "SELECT 1")
