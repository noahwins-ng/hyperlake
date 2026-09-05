data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

data "aws_iam_policy_document" "github_oidc_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Repo-scoped, any branch/tag/environment within it.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "hyperlake-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_oidc_trust.json
}

data "aws_iam_policy_document" "github_actions_permissions" {
  # Athena/Glue/S3 resources below are scoped by name prefix ahead of their
  # creation (data bucket + workgroup land in QNT-450) rather than left "*".
  statement {
    sid = "AthenaWorkgroup"
    actions = [
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = [
      "arn:aws:athena:ap-northeast-1:${data.aws_caller_identity.current.account_id}:workgroup/hyperlake*",
    ]
  }

  statement {
    sid = "GlueCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchCreatePartition",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
    ]
    resources = [
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:database/hyperlake*",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:table/hyperlake*/*",
    ]
  }

  statement {
    sid = "DataBuckets"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      "arn:aws:s3:::hyperlake-*",
      "arn:aws:s3:::hyperlake-*/*",
    ]
  }

  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:ap-northeast-1:${data.aws_caller_identity.current.account_id}:log-group:/hyperlake/*",
    ]
  }
}

resource "aws_iam_role_policy" "github_actions" {
  name   = "hyperlake-github-actions-permissions"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_permissions.json
}
