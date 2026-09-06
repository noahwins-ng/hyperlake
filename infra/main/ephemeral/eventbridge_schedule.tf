# QNT-452 scope: a daily "yesterday" catch-up schedule for the backfill state machine,
# disabled by default. The input below is a placeholder, not a real yesterday's hour
# list -- computing that dynamically at trigger time is deferred to `make heal`
# (QNT-461), which reuses this same state machine for gap-healing. Enabling this
# schedule today would replay the same fixed hour list every day; wire a real input
# (or flip this off in favor of QNT-461's own trigger) before ever setting `state` to
# "ENABLED". Documented in docs/guides/ops-runbook.md.
data "aws_iam_policy_document" "backfill_schedule_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backfill_schedule" {
  name               = "hyperlake-backfill-schedule"
  assume_role_policy = data.aws_iam_policy_document.backfill_schedule_trust.json
}

data "aws_iam_policy_document" "backfill_schedule_permissions" {
  statement {
    sid       = "StartBackfillExecution"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.backfill.arn]
  }
}

resource "aws_iam_role_policy" "backfill_schedule" {
  name   = "hyperlake-backfill-schedule-permissions"
  role   = aws_iam_role.backfill_schedule.id
  policy = data.aws_iam_policy_document.backfill_schedule_permissions.json
}

resource "aws_scheduler_schedule" "backfill_daily_catchup" {
  name       = "hyperlake-backfill-daily-catchup"
  state      = "DISABLED"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "cron(0 3 * * ? *)"

  target {
    arn      = aws_sfn_state_machine.backfill.arn
    role_arn = aws_iam_role.backfill_schedule.arn
    # Placeholder -- see the file header comment. Never a real "yesterday" until QNT-461.
    input = jsonencode({ hours = [] })
  }
}
