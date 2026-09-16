from unittest.mock import MagicMock

from scripts.audit_teardown import billable_ephemeral_arns, check, tagged_resource_arns

KINESIS_ARN = "arn:aws:kinesis:ap-northeast-1:380345540395:stream/hyperlake-trades"
FIREHOSE_ARN = "arn:aws:firehose:ap-northeast-1:380345540395:deliverystream/hyperlake-trades"
SCHEDULER_ARN = "arn:aws:scheduler:ap-northeast-1:380345540395:schedule/default/hyperlake-reaper"
ECS_ARN = "arn:aws:ecs:ap-northeast-1:380345540395:service/hyperlake/hyperlake-ingester"
ECS_CLUSTER_ARN = "arn:aws:ecs:ap-northeast-1:380345540395:cluster/hyperlake"
ECS_TASK_DEF_ARN = "arn:aws:ecs:ap-northeast-1:380345540395:task-definition/hyperlake-ingester:3"
S3_ARN = "arn:aws:s3:::hyperlake-data-380345540395"
GLUE_ARN = "arn:aws:glue:ap-northeast-1:380345540395:database/bronze"


def _mock_tagging(arns: list[str]) -> MagicMock:
    tagging = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"ResourceTagMappingList": [{"ResourceARN": a} for a in arns]}
    ]
    tagging.get_paginator.return_value = paginator
    return tagging


def _mock_ecs(services_by_cluster: dict[str, list[dict]]) -> MagicMock:
    ecs = MagicMock()

    def describe_services(cluster, services):
        return {"services": services_by_cluster[cluster]}

    ecs.describe_services.side_effect = describe_services
    return ecs


def test_tagged_resource_arns_paginates_across_pages():
    tagging = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"ResourceTagMappingList": [{"ResourceARN": KINESIS_ARN}]},
        {"ResourceTagMappingList": [{"ResourceARN": FIREHOSE_ARN}]},
    ]
    tagging.get_paginator.return_value = paginator
    assert tagged_resource_arns(tagging) == [KINESIS_ARN, FIREHOSE_ARN]


def test_billable_ephemeral_arns_excludes_persistent_layer_services():
    result = billable_ephemeral_arns([S3_ARN, GLUE_ARN, KINESIS_ARN], _mock_ecs({}))
    assert result == [KINESIS_ARN]


def test_billable_ephemeral_arns_includes_kinesis_firehose_scheduler_unconditionally():
    result = billable_ephemeral_arns([KINESIS_ARN, FIREHOSE_ARN, SCHEDULER_ARN], _mock_ecs({}))
    assert set(result) == {KINESIS_ARN, FIREHOSE_ARN, SCHEDULER_ARN}


def test_billable_ephemeral_arns_flags_ecs_service_with_nonzero_desired_count():
    ecs = _mock_ecs({"hyperlake": [{"serviceArn": ECS_ARN, "desiredCount": 1, "runningCount": 1}]})
    assert billable_ephemeral_arns([ECS_ARN], ecs) == [ECS_ARN]


def test_billable_ephemeral_arns_excludes_ecs_service_scaled_to_zero():
    ecs = _mock_ecs({"hyperlake": [{"serviceArn": ECS_ARN, "desiredCount": 0, "runningCount": 0}]})
    assert billable_ephemeral_arns([ECS_ARN], ecs) == []


def test_billable_ephemeral_arns_ignores_ecs_cluster_and_task_definition_arns():
    # Regression: provider default_tags also tags the (free) ECS cluster and task
    # definition alongside the service -- describe_services must never be called
    # with one of those (it previously crashed with InvalidParameterException,
    # caught running this live against the real AWS account).
    ecs = _mock_ecs({"hyperlake": [{"serviceArn": ECS_ARN, "desiredCount": 1, "runningCount": 1}]})
    result = billable_ephemeral_arns([ECS_CLUSTER_ARN, ECS_TASK_DEF_ARN, ECS_ARN], ecs)
    assert result == [ECS_ARN]
    ecs.describe_services.assert_called_once_with(cluster="hyperlake", services=[ECS_ARN])


def test_check_reports_no_findings_when_nothing_billable_tagged():
    tagging = _mock_tagging([S3_ARN, GLUE_ARN])
    assert check(tagging, _mock_ecs({})) == []


def test_check_reports_sorted_findings():
    tagging = _mock_tagging([FIREHOSE_ARN, KINESIS_ARN])
    assert check(tagging, _mock_ecs({})) == sorted([FIREHOSE_ARN, KINESIS_ARN])
