# QNT-455: Phase 2 spike. Kinesis (replay buffer, partition key = coin) -> Firehose
# (Parquet conversion against the persistent layer's bronze.trades_raw Glue schema +
# dynamic partitioning on coin/dt) -- ADR-004. source=ws/ is a literal prefix segment,
# not JQ-derived: this stream only ever carries the live WS path.

resource "aws_kinesis_stream" "trades" {
  name = "hyperlake-trades"

  stream_mode_details {
    stream_mode = "ON_DEMAND"
  }
}

data "aws_iam_policy_document" "firehose_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "hyperlake-firehose-trades"
  assume_role_policy = data.aws_iam_policy_document.firehose_trust.json
}

# Scoped to: read the one Kinesis stream, read the bronze.trades_raw Glue table (schema
# for Parquet conversion only -- Firehose never writes to Glue), write bronze/+errors/
# under the persistent data bucket, and its own log group.
data "aws_iam_policy_document" "firehose_permissions" {
  statement {
    sid       = "KinesisRead"
    actions   = ["kinesis:DescribeStream", "kinesis:GetShardIterator", "kinesis:GetRecords", "kinesis:ListShards"]
    resources = [aws_kinesis_stream.trades.arn]
  }

  statement {
    sid     = "GlueSchema"
    actions = ["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"]
    resources = [
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:database/${data.terraform_remote_state.persistent.outputs.glue_database_name}",
      "arn:aws:glue:ap-northeast-1:${data.aws_caller_identity.current.account_id}:table/${data.terraform_remote_state.persistent.outputs.glue_database_name}/trades_raw",
    ]
  }

  statement {
    sid     = "BronzeWrite"
    actions = ["s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${data.terraform_remote_state.persistent.outputs.data_bucket_name}",
      "arn:aws:s3:::${data.terraform_remote_state.persistent.outputs.data_bucket_name}/bronze/*",
      "arn:aws:s3:::${data.terraform_remote_state.persistent.outputs.data_bucket_name}/errors/*",
    ]
  }

  statement {
    sid     = "Logs"
    actions = ["logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:ap-northeast-1:${data.aws_caller_identity.current.account_id}:log-group:/aws/kinesisfirehose/hyperlake-trades:*",
    ]
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "hyperlake-firehose-trades-permissions"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose_permissions.json
}

# Declared explicitly with a short retention (AC-templates: Terraform / session-lifecycle)
# so it's destroyed with the rest of the ephemeral stack.
resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/kinesisfirehose/hyperlake-trades"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_stream" "firehose_delivery" {
  name           = "S3Delivery"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

resource "aws_kinesis_firehose_delivery_stream" "trades" {
  name        = "hyperlake-trades"
  destination = "extended_s3"

  kinesis_source_configuration {
    kinesis_stream_arn = aws_kinesis_stream.trades.arn
    role_arn           = aws_iam_role.firehose.arn
  }

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = "arn:aws:s3:::${data.terraform_remote_state.persistent.outputs.data_bucket_name}"

    prefix              = "bronze/coin=!{partitionKeyFromQuery:coin}/dt=!{partitionKeyFromQuery:dt}/source=ws/"
    error_output_prefix = "errors/firehose/!{firehose:error-output-type}/"

    buffering_size     = 64
    buffering_interval = 60

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = aws_cloudwatch_log_stream.firehose_delivery.name
    }

    dynamic_partitioning_configuration {
      enabled = true
    }

    # JQ derives the two dynamic-partitioning keys straight from the envelope: coin's
    # ':' -> '_' normalisation (mirrors hyperlake.partitions.coin_partition_value, the
    # one other normalisation call site, per CLAUDE.md) and dt from event `time`
    # (epoch ms), never arrival time.
    processing_configuration {
      enabled = true

      processors {
        type = "MetadataExtraction"

        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{coin: (.coin | gsub(\":\"; \"_\")), dt: (.time / 1000 | floor | gmtime | strftime(\"%Y-%m-%d\"))}"
        }
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
      }

      processors {
        type = "AppendDelimiterToRecord"
      }
    }

    # Parquet conversion against the shared envelope schema already registered on
    # bronze.trades_raw (persistent/glue.tf) -- no schema typed a second time here.
    # `millis` tells the input deserializer that `time`/`ingested_at` are Unix epoch
    # milliseconds, not Hive's default timestamp string format.
    data_format_conversion_configuration {
      enabled = true

      input_format_configuration {
        deserializer {
          hive_json_ser_de {
            timestamp_formats = ["millis"]
          }
        }
      }

      output_format_configuration {
        serializer {
          parquet_ser_de {}
        }
      }

      schema_configuration {
        database_name = data.terraform_remote_state.persistent.outputs.glue_database_name
        table_name    = "trades_raw"
        role_arn      = aws_iam_role.firehose.arn
        region        = "ap-northeast-1"
      }
    }
  }

  depends_on = [aws_iam_role_policy.firehose]
}

# CLAUDE.md ingestion-contract failure mode: failed records land under errors/, and this
# alarm is the visible signal during a session (PRD "Firehose delivery failure" risk row).
resource "aws_cloudwatch_metric_alarm" "firehose_delivery_failures" {
  alarm_name          = "hyperlake-firehose-trades-delivery-failures"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DeliveryToS3.Success"
  namespace           = "AWS/Firehose"
  period              = 300
  statistic           = "Average"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_description   = "Firehose failed to deliver records to S3 -- check the errors/ prefix."

  dimensions = {
    DeliveryStreamName = aws_kinesis_firehose_delivery_stream.trades.name
  }
}
