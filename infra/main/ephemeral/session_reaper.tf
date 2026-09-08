# QNT-459: the FR-8 dead-man's switch. A per-session one-time EventBridge Scheduler `at()`
# entry (the `aws_scheduler_schedule.session_reaper` resource below, created by every
# `session-up` apply and torn down by every `session-down` destroy) invokes this Lambda if
# nobody tears the session down within `max_session_hours`. It scales the ingester to 0,
# waits for the Firehose buffer to drain, deletes the Kinesis stream, and writes a reap
# marker to S3 -- the only channel back to `session-down`, which has no other way to learn
# the switch fired.
#
# Packaged straight from source (no third-party deps beyond boto3, which ships with the
# Lambda runtime) via `archive_file` -- unlike backfill_lambda.tf's `data "external"` +
# `uv pip install` build, there's nothing to compile here.
data "archive_file" "session_reaper_lambda" {
  type        = "zip"
  output_path = "${path.module}/build/session_reaper_lambda.zip"

  source {
    content  = file("${path.module}/../../../src/hyperlake/__init__.py")
    filename = "hyperlake/__init__.py"
  }
  source {
    content  = file("${path.module}/../../../src/hyperlake/session.py")
    filename = "hyperlake/session.py"
  }
  source {
    content  = file("${path.module}/../../../src/hyperlake/session_reaper.py")
    filename = "hyperlake/session_reaper.py"
  }
}

data "aws_iam_policy_document" "session_reaper_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "session_reaper" {
  name               = "hyperlake-session-reaper"
  assume_role_policy = data.aws_iam_policy_document.session_reaper_trust.json
}

# Scoped to exactly the three actions the reap does: scale the one ingester service to 0,
# delete the one trades stream, and write under the data bucket's `sessions/` prefix.
data "aws_iam_policy_document" "session_reaper_permissions" {
  statement {
    sid       = "ScaleIngesterToZero"
    actions   = ["ecs:UpdateService"]
    resources = [aws_ecs_service.ingester.id]
  }

  statement {
    sid       = "DeleteTradesStream"
    actions   = ["kinesis:DeleteStream"]
    resources = [aws_kinesis_stream.trades.arn]
  }

  statement {
    sid       = "WriteReapMarker"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${data.terraform_remote_state.persistent.outputs.data_bucket_name}/sessions/*"]
  }

  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "${aws_cloudwatch_log_group.session_reaper.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "session_reaper" {
  name   = "hyperlake-session-reaper-permissions"
  role   = aws_iam_role.session_reaper.id
  policy = data.aws_iam_policy_document.session_reaper_permissions.json
}

# Declared explicitly with a short retention so it's destroyed with the rest of the
# ephemeral stack and never grows unbounded (AC-templates: Terraform / session-lifecycle).
resource "aws_cloudwatch_log_group" "session_reaper" {
  name              = "/aws/lambda/hyperlake-session-reaper"
  retention_in_days = 14
}

resource "aws_lambda_function" "session_reaper" {
  function_name = "hyperlake-session-reaper"
  role          = aws_iam_role.session_reaper.arn
  handler       = "hyperlake.session_reaper.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  memory_size   = 128
  # >= scale-to-zero + the 120s drain wait (hyperlake.session.DRAIN_SECONDS) + delete-stream,
  # with margin.
  timeout          = 210
  filename         = data.archive_file.session_reaper_lambda.output_path
  source_code_hash = data.archive_file.session_reaper_lambda.output_base64sha256

  depends_on = [
    aws_cloudwatch_log_group.session_reaper,
    aws_iam_role_policy.session_reaper,
  ]
}

# The role EventBridge Scheduler assumes to invoke the reaper -- referenced as `role_arn` by
# the `aws_scheduler_schedule.session_reaper` resource below.
data "aws_iam_policy_document" "session_reaper_schedule_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "session_reaper_schedule" {
  name               = "hyperlake-session-reaper-schedule"
  assume_role_policy = data.aws_iam_policy_document.session_reaper_schedule_trust.json
}

data "aws_iam_policy_document" "session_reaper_schedule_permissions" {
  statement {
    sid       = "InvokeReaperLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.session_reaper.arn]
  }
}

resource "aws_iam_role_policy" "session_reaper_schedule" {
  name   = "hyperlake-session-reaper-schedule-permissions"
  role   = aws_iam_role.session_reaper_schedule.id
  policy = data.aws_iam_policy_document.session_reaper_schedule_permissions.json
}

# The per-session dead-man's switch itself. Unlike eventbridge_schedule.tf's
# `backfill_daily_catchup` (static, always present, fixed cron), this is created fresh by
# every `session-up` apply and torn down by every `session-down` destroy -- Terraform-native
# (visible to `terraform plan`/state, never hand-created) rather than a boto3 side-call, per
# CLAUDE.md's "everything is Terraform" rule. (Like `backfill_daily_catchup`, this resource
# type has no `tags` attribute in this provider version -- individual schedules aren't
# taggable via this API, only schedule groups are, so `project=hyperlake` doesn't apply here
# any more than it does to the existing static schedule.) `session-down`'s unconditional
# `terraform destroy` removes it the same way it already tolerates the Kinesis stream being
# gone (AC2) -- if the reaper fired first and already deleted the stream, destroy just
# proceeds past the missing resource on refresh; this schedule itself has already served its
# purpose by firing and stays in state until that destroy runs (no auto-delete-on-completion
# in this provider version).
resource "aws_scheduler_schedule" "session_reaper" {
  name       = "hyperlake-reaper-${var.session_id}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "at(${formatdate("YYYY-MM-DD'T'hh:mm:ss", timeadd(var.session_start, "${var.max_session_hours}h"))})"
  # timeadd/formatdate above compute in the source zone (UTC, since session_start is always
  # `Z`-suffixed) -- this just tells the scheduler to interpret the at() string as UTC too.
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.session_reaper.arn
    role_arn = aws_iam_role.session_reaper_schedule.arn

    input = jsonencode({
      session_id  = var.session_id
      cluster     = aws_ecs_cluster.hyperlake.name
      service     = aws_ecs_service.ingester.name
      stream_name = aws_kinesis_stream.trades.name
      bucket      = data.terraform_remote_state.persistent.outputs.data_bucket_name
    })
  }

  depends_on = [aws_iam_role_policy.session_reaper_schedule]
}
