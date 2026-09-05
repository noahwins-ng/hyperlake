resource "aws_budgets_budget" "monthly" {
  name         = "hyperlake-monthly"
  budget_type  = "COST"
  limit_amount = "10"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}

# Lagging backstop only (NFR-1): this denies *creating* more of the costly
# services, it never deletes/stops anything already running. session-down and
# the session reaper are what actually stop the meter.
resource "aws_iam_policy" "budget_deny_create" {
  name = "hyperlake-budget-deny-create"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyCreateOnOverspend"
      Effect = "Deny"
      Action = [
        "kinesis:CreateStream",
        "ecs:CreateService",
        "ecs:RunTask",
        "lambda:CreateFunction",
      ]
      Resource = "*"
    }]
  })
}

data "aws_iam_policy_document" "budgets_action_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    # Confused-deputy hardening: only this account's Budgets service may assume it.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "budgets_action" {
  name               = "hyperlake-budgets-action"
  assume_role_policy = data.aws_iam_policy_document.budgets_action_trust.json
}

resource "aws_iam_role_policy" "budgets_action_permissions" {
  name = "hyperlake-budgets-action-permissions"
  role = aws_iam_role.budgets_action.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
      ]
      Resource = aws_iam_role.github_actions.arn
    }]
  })
}

resource "aws_budgets_budget_action" "deny_create_at_15" {
  budget_name       = aws_budgets_budget.monthly.name
  action_type       = "APPLY_IAM_POLICY"
  approval_model    = "AUTOMATIC"
  notification_type = "ACTUAL"

  action_threshold {
    action_threshold_type  = "ABSOLUTE_VALUE"
    action_threshold_value = 15
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.budget_deny_create.arn
      roles      = [aws_iam_role.github_actions.name]
    }
  }

  execution_role_arn = aws_iam_role.budgets_action.arn

  subscriber {
    address           = var.budget_notification_email
    subscription_type = "EMAIL"
  }
}
