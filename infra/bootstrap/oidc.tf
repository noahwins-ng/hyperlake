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
    # Full read+write set per dbt-athena's documented IAM requirements
    # (https://dbt-athena.github.io/docs/getting-started/prerequisites/iam-permissions) --
    # Iceberg's merge/incremental strategy exercises the table-version and partition-batch
    # actions beyond what a plain-table adapter would need.
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:CreateDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersions",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchCreatePartition",
      "glue:BatchUpdatePartition",
      "glue:BatchDeletePartition",
      "glue:BatchDeleteTable",
      "glue:BatchDeleteTableVersion",
      "glue:CreatePartition",
      "glue:UpdatePartition",
      "glue:DeletePartition",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
      "glue:DeleteTableVersion",
    ]
    # Scoped to the real layer database names (infra/main/persistent/glue.tf, dbt schema
    # default) -- "hyperlake*" was never a real prefix any Glue database used, so this
    # statement never actually covered the catalog until this fix (QNT-473).
    resources = [
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:database/bronze",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:database/silver",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:database/gold",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:table/bronze/*",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:table/silver/*",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:table/gold/*",
    ]
  }

  statement {
    sid = "DataBuckets"
    # Full read+write set per dbt-athena's documented IAM requirements (see GlueCatalog
    # above) -- multipart-upload actions cover Iceberg's larger metadata/data file writes.
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:ListBucketMultipartUploads",
      "s3:ListMultipartUploadParts",
      "s3:AbortMultipartUpload",
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
