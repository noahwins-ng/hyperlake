import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.backfill import failed_hours, run_backfill


def test_failed_hours_returns_only_failed_entries():
    ok_entry = {"date": "20260903", "hour": "0", "result": {"status": "ok"}}
    failed_entry = {
        "date": "20260903",
        "hour": "1",
        "status": "failed",
        "error": {"Error": "States.Timeout"},
    }
    output = {"results": [ok_entry, failed_entry]}
    assert failed_hours(output) == [failed_entry]


def test_failed_hours_empty_when_all_succeed():
    output = {"results": [{"date": "20260903", "hour": "0", "result": {"status": "ok"}}]}
    assert failed_hours(output) == []


@patch("scripts.backfill.time.sleep")
def test_run_backfill_polls_until_succeeded_and_returns_output(mock_sleep):
    sfn = MagicMock()
    sfn.start_execution.return_value = {"executionArn": "arn:aws:states:x:execution:x"}
    sfn.describe_execution.side_effect = [
        {"status": "RUNNING"},
        {"status": "SUCCEEDED", "output": json.dumps({"hours": [], "results": []})},
    ]

    result = run_backfill(
        sfn, "arn:aws:states:x:stateMachine:hyperlake-backfill", "2026-09-03", "2026-09-03"
    )

    assert result["output"] == {"hours": [], "results": []}
    assert mock_sleep.call_count == 1
    started_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    assert len(started_input["hours"]) == 25


@patch("scripts.backfill.time.sleep")
def test_run_backfill_raises_on_non_succeeded_terminal_status(mock_sleep):
    sfn = MagicMock()
    sfn.start_execution.return_value = {"executionArn": "arn:aws:states:x:execution:x"}
    sfn.describe_execution.return_value = {"status": "FAILED", "cause": "boom"}

    with pytest.raises(RuntimeError):
        run_backfill(sfn, "arn", "2026-09-03", "2026-09-03")
