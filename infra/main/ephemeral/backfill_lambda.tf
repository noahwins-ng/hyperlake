# QNT-451: the official-archive backfill reader. One invocation processes one hour
# file; `hyperlake.backfill.official.handler` does the work, this just wires it up.
#
# The deployment zip is built by scripts/build_backfill_lambda.sh, not by a Terraform
# provisioner: `data "external"` runs it at plan/apply time only (mirrors persistent/
# glue.tf's envelope-schema pattern) -- never at `terraform validate`, so the offline
# `make tf-check` gate never runs `uv pip install` or needs network access.
data "external" "backfill_lambda_zip" {
  program = ["${path.module}/../../../scripts/build_backfill_lambda.sh", "--json"]
}

data "aws_iam_policy_document" "backfill_lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backfill_lambda" {
  name               = "hyperlake-backfill-lambda"
  assume_role_policy = data.aws_iam_policy_document.backfill_lambda_trust.json
}

# Scoped to exactly the two buckets this reader touches: the official requester-pays
# archive (read-only, under the one prefix it reads) and our own bronze prefix (write).
data "aws_iam_policy_document" "backfill_lambda_permissions" {
  statement {
    sid       = "ArchiveRead"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::hl-mainnet-node-data/node_fills_by_block/*"]
  }

  statement {
    sid       = "BronzeWrite"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${data.terraform_remote_state.persistent.outputs.data_bucket_name}/bronze/*"]
  }

  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:ap-northeast-1:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/hyperlake-backfill-official:*",
    ]
  }
}

resource "aws_iam_role_policy" "backfill_lambda" {
  name   = "hyperlake-backfill-lambda-permissions"
  role   = aws_iam_role.backfill_lambda.id
  policy = data.aws_iam_policy_document.backfill_lambda_permissions.json
}

# Declared explicitly with a short retention so it's destroyed with the rest of the
# ephemeral stack and never grows unbounded (AC-templates: Terraform / session-lifecycle).
resource "aws_cloudwatch_log_group" "backfill_lambda" {
  name              = "/aws/lambda/hyperlake-backfill-official"
  retention_in_days = 14
}

resource "aws_lambda_function" "backfill_official" {
  function_name    = "hyperlake-backfill-official"
  role             = aws_iam_role.backfill_lambda.arn
  handler          = "hyperlake.backfill.official.handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  memory_size      = 2048
  timeout          = 300
  filename         = data.external.backfill_lambda_zip.result.path
  source_code_hash = data.external.backfill_lambda_zip.result.sha256_base64

  environment {
    variables = {
      HYPERLAKE_DATA_BUCKET = data.terraform_remote_state.persistent.outputs.data_bucket_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.backfill_lambda,
    aws_iam_role_policy.backfill_lambda,
  ]
}
