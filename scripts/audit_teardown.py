#!/usr/bin/env python3
"""Post-destroy audit (QNT-471, FR-6): list any live `project=hyperlake` billable
ephemeral resources and fail loudly if any remain -- a missed resource is a silent
monthly burn. Persistent-layer resources (S3, Glue, Athena workgroup, ECR) are
expected to stay and are out of scope; they are never in `BILLABLE_SERVICES` so
they can never be flagged even if incidentally tagged.
"""

import boto3

REGION = "ap-northeast-1"
TAG_KEY = "project"
TAG_VALUE = "hyperlake"

# (service, resource type) pairs that are billable the moment they exist --
# session-down tears these down outright, so any live one is unconditionally
# flagged. An ECS *service* is the one exception (handled separately below):
# provider default_tags also tags the ECS *cluster* (a free construct) and the
# task definition, which must never be treated as billable resources.
UNCONDITIONAL_TYPES = frozenset(
    {("kinesis", "stream"), ("firehose", "deliverystream"), ("scheduler", "schedule")}
)


def _resource_type(arn: str) -> str:
    """The resource-type segment of an ARN, e.g. "service" out of
    "arn:aws:ecs:region:account:service/cluster/name"."""
    return arn.split(":", 5)[5].split("/")[0]


def tagged_resource_arns(tagging_client) -> list[str]:
    """Every resource ARN tagged `project=hyperlake`, across all resource types."""
    arns = []
    paginator = tagging_client.get_paginator("get_resources")
    for page in paginator.paginate(TagFilters=[{"Key": TAG_KEY, "Values": [TAG_VALUE]}]):
        arns.extend(m["ResourceARN"] for m in page["ResourceTagMappingList"])
    return arns


def _live_ecs_services(service_arns: list[str], ecs_client) -> set[str]:
    """`service_arns` (`.../service/<cluster>/<service>`) whose live desired or
    running task count is nonzero -- session-down deletes the service outright,
    so a scaled-to-zero-but-not-yet-deleted service is the only case this needs
    to distinguish from a truly gone one."""
    by_cluster: dict[str, list[str]] = {}
    for arn in service_arns:
        cluster = arn.split("/")[-2]
        by_cluster.setdefault(cluster, []).append(arn)

    live = set()
    for cluster, arns in by_cluster.items():
        resp = ecs_client.describe_services(cluster=cluster, services=arns)
        for svc in resp["services"]:
            if svc["desiredCount"] > 0 or svc["runningCount"] > 0:
                live.add(svc["serviceArn"])
    return live


def billable_ephemeral_arns(arns: list[str], ecs_client) -> list[str]:
    """`arns` filtered down to the ones that are actually billable right now."""
    service_arns = [a for a in arns if a.split(":")[2] == "ecs" and _resource_type(a) == "service"]
    live_ecs = _live_ecs_services(service_arns, ecs_client) if service_arns else set()

    return [
        a
        for a in arns
        if (a.split(":")[2], _resource_type(a)) in UNCONDITIONAL_TYPES or a in live_ecs
    ]


def check(tagging_client, ecs_client) -> list[str]:
    """Billable `project=hyperlake` resource ARNs still live, sorted -- empty
    means teardown left nothing billable behind."""
    arns = tagged_resource_arns(tagging_client)
    return sorted(billable_ephemeral_arns(arns, ecs_client))


def main() -> None:
    findings = check(
        boto3.client("resourcegroupstaggingapi", region_name=REGION),
        boto3.client("ecs", region_name=REGION),
    )

    if findings:
        for arn in findings:
            print(f"LIVE BILLABLE RESOURCE: {arn}")
        raise SystemExit(1)
    print("no billable ephemeral resources")


if __name__ == "__main__":
    main()
