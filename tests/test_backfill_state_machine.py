from hyperlake.backfill.state_machine import render_definition

LAMBDA_ARN = "arn:aws:lambda:ap-northeast-1:123456789012:function:hyperlake-backfill-official"


def _map_task():
    definition = render_definition(LAMBDA_ARN)
    processor_states = definition["States"]["BackfillHours"]["ItemProcessor"]["States"]
    return definition, processor_states


def test_top_level_state_is_a_map_over_hours():
    definition, _ = _map_task()
    map_state = definition["States"]["BackfillHours"]
    assert map_state["Type"] == "Map"
    assert map_state["ItemsPath"] == "$.hours"
    assert map_state["MaxConcurrency"] == 10


def test_lambda_arn_is_substituted_into_the_invoke_task():
    _, states = _map_task()
    task = states["InvokeBackfillLambda"]
    assert task["Parameters"]["FunctionName"] == LAMBDA_ARN


def test_invoke_task_retries_on_throttling_and_timeout():
    _, states = _map_task()
    task = states["InvokeBackfillLambda"]
    retry_errors = {e for r in task["Retry"] for e in r["ErrorEquals"]}
    assert {"Lambda.TooManyRequestsException", "States.Timeout"} <= retry_errors


def test_invoke_task_catches_and_surfaces_failed_hours():
    _, states = _map_task()
    task = states["InvokeBackfillLambda"]
    catch = task["Catch"][0]
    assert catch["ErrorEquals"] == ["States.ALL"]
    fail_state = states[catch["Next"]]
    assert fail_state["Parameters"]["status"] == "failed"
    assert fail_state["Parameters"]["date.$"] == "$.date"
    assert fail_state["Parameters"]["hour.$"] == "$.hour"
