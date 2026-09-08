# QNT-457: runs the live ingester image (Dockerfile, QNT-456) on Fargate. Default VPC,
# public IP, egress-only security group -- no VPC resources of our own (NFR-2, CLAUDE.md).
# `aws_ecs_service.ingester` starts at desired_count 0; `make ingester-start`/`ingester-stop`
# (aws ecs update-service) are the primitives session-up/down will call.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# No ingress rules -- the task only ever makes outbound calls (Hyperliquid WS, Kinesis
# PutRecords). Open egress because there's no VPC endpoint plumbing of our own (NFR-2).
resource "aws_security_group" "ingester" {
  name        = "hyperlake-ingester"
  description = "Egress-only: outbound to Hyperliquid WS + AWS APIs, no inbound"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "hyperlake" {
  name = "hyperlake"
}

# Declared explicitly with a short retention so it's destroyed with the rest of the
# ephemeral stack and never grows unbounded (AC-templates: Terraform / session-lifecycle).
resource "aws_cloudwatch_log_group" "ingester" {
  name              = "/ecs/hyperlake-ingester"
  retention_in_days = 14
}

data "aws_iam_policy_document" "ecs_task_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: what ECS itself needs to start the task (pull the image, ship its logs).
# Scoped custom policy rather than the AWS-managed AmazonECSTaskExecutionRolePolicy, matching
# this repo's pattern of naming exactly what each role touches (backfill_lambda.tf, kinesis_firehose.tf).
resource "aws_iam_role" "ingester_execution" {
  name               = "hyperlake-ingester-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_trust.json
}

data "aws_iam_policy_document" "ingester_execution_permissions" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "EcrPull"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = ["arn:aws:ecr:ap-northeast-1:${data.aws_caller_identity.current.account_id}:repository/hyperlake-ingester"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingester.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ingester_execution" {
  name   = "hyperlake-ingester-execution-permissions"
  role   = aws_iam_role.ingester_execution.id
  policy = data.aws_iam_policy_document.ingester_execution_permissions.json
}

# Task role: what the running container itself is allowed to call -- exactly
# `kinesis:PutRecords` on the one stream (ticket scope), nothing else.
resource "aws_iam_role" "ingester_task" {
  name               = "hyperlake-ingester-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_trust.json
}

data "aws_iam_policy_document" "ingester_task_permissions" {
  statement {
    sid       = "KinesisPutRecords"
    actions   = ["kinesis:PutRecords"]
    resources = [aws_kinesis_stream.trades.arn]
  }
}

resource "aws_iam_role_policy" "ingester_task" {
  name   = "hyperlake-ingester-task-permissions"
  role   = aws_iam_role.ingester_task.id
  policy = data.aws_iam_policy_document.ingester_task_permissions.json
}

resource "aws_ecs_task_definition" "ingester" {
  family                   = "hyperlake-ingester"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ingester_execution.arn
  task_role_arn            = aws_iam_role.ingester_task.arn

  container_definitions = jsonencode([
    {
      name  = "ingester"
      image = "${data.terraform_remote_state.persistent.outputs.ecr_repository_url}:${var.image_tag}"
      # Watchlist path comes from the image's baked-in default (Dockerfile COPYs
      # config/watchlist.yaml to the src-layout path hyperlake.watchlist resolves by
      # default) -- no override needed. SESSION_ID is deliberately not set here: the
      # ingester self-generates one per process start (no --session-id CLI flag exists
      # yet); a real session-scoped id is QNT-458's concern.
      command = [
        "--stream-name", aws_kinesis_stream.trades.name,
        "--max-session-hours", tostring(var.max_session_hours),
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ingester.name
          "awslogs-region"        = "ap-northeast-1"
          "awslogs-stream-prefix" = "ingester"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "ingester" {
  name            = "hyperlake-ingester"
  cluster         = aws_ecs_cluster.hyperlake.id
  task_definition = aws_ecs_task_definition.ingester.arn
  launch_type     = "FARGATE"
  desired_count   = 0

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ingester.id]
    assign_public_ip = true
  }

  # `make ingester-start`/`ingester-stop` (Makefile) flip the live desired count
  # out-of-band via `aws ecs update-service` -- without this, a `terraform apply` re-run
  # mid-session (e.g. an unrelated ephemeral change) silently scales a running ingester
  # back to 0. Confirmed live during QNT-457 verification: a re-apply reset desired_count
  # and had to be followed by another `make ingester-start`.
  lifecycle {
    ignore_changes = [desired_count]
  }
}
