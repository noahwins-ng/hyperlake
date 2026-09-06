# QNT-452: Step Functions fan-out over the backfill Lambda (ADR-001 -- Step Functions
# scoped to orchestration only, never dbt). One Map iteration per archive hour file;
# `scripts/backfill.py` builds the `{hours: [...]}` input and starts the execution.
data "aws_iam_policy_document" "backfill_state_machine_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backfill_state_machine" {
  name               = "hyperlake-backfill-state-machine"
  assume_role_policy = data.aws_iam_policy_document.backfill_state_machine_trust.json
}

data "aws_iam_policy_document" "backfill_state_machine_permissions" {
  statement {
    sid       = "InvokeBackfillLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.backfill_official.arn]
  }
}

resource "aws_iam_role_policy" "backfill_state_machine" {
  name   = "hyperlake-backfill-state-machine-permissions"
  role   = aws_iam_role.backfill_state_machine.id
  policy = data.aws_iam_policy_document.backfill_state_machine_permissions.json
}

resource "aws_sfn_state_machine" "backfill" {
  name     = "hyperlake-backfill"
  role_arn = aws_iam_role.backfill_state_machine.arn
  definition = templatefile("${path.module}/state_machine/backfill.asl.json", {
    lambda_arn = aws_lambda_function.backfill_official.arn
  })
}
