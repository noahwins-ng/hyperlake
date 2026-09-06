#!/usr/bin/env python3
"""Fan out the backfill Lambda over an hour range through the Step Functions state
machine (QNT-452). Builds the hour list (boundary-rule trailing hour included), starts
one execution, waits for it, and reports any failed hours.
"""

import argparse
import json
import time

import boto3

from hyperlake.backfill.hour_list import hour_list

POLL_INTERVAL_SECONDS = 5
REGION = "ap-northeast-1"


def failed_hours(output: dict) -> list[dict]:
    """Hours the Map marked failed (`MarkHourFailed`'s `status: "failed"` items)."""
    return [r for r in output.get("results", []) if r.get("status") == "failed"]


def run_backfill(sfn_client, state_machine_arn: str, from_date: str, to_date: str) -> dict:
    """Start one execution over `[from_date, to_date]` and wait for it to finish.
    Returns `{"output": <parsed execution output>, "wall_seconds": <float>}`."""
    hours = hour_list(from_date, to_date)
    name = f"backfill-{from_date}-{to_date}-{int(time.time())}"
    start = sfn_client.start_execution(
        stateMachineArn=state_machine_arn,
        name=name,
        input=json.dumps({"hours": hours}),
    )
    started_at = time.monotonic()

    while True:
        desc = sfn_client.describe_execution(executionArn=start["executionArn"])
        if desc["status"] != "RUNNING":
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    wall_seconds = time.monotonic() - started_at
    if desc["status"] != "SUCCEEDED":
        raise RuntimeError(f"execution {desc['status']}: {desc.get('cause', '')}")
    return {"output": json.loads(desc["output"]), "wall_seconds": wall_seconds}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", required=True, help="ISO date, inclusive")
    parser.add_argument("--to", dest="to_date", required=True, help="ISO date, inclusive")
    parser.add_argument("--state-machine-arn", required=True)
    args = parser.parse_args()

    sfn = boto3.client("stepfunctions", region_name=REGION)
    result = run_backfill(sfn, args.state_machine_arn, args.from_date, args.to_date)

    failed = failed_hours(result["output"])
    print(f"wall time: {result['wall_seconds']:.1f}s")
    print(f"hours run: {len(result['output'].get('results', []))}, failed: {len(failed)}")
    for f in failed:
        print(f"  FAILED date={f['date']} hour={f['hour']} error={f.get('error')}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
