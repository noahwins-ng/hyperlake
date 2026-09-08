output "backfill_state_machine_arn" {
  value = aws_sfn_state_machine.backfill.arn
}

output "kinesis_stream_name" {
  value = aws_kinesis_stream.trades.name
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.hyperlake.name
}

output "ecs_service_name" {
  value = aws_ecs_service.ingester.name
}

# Passthrough of the persistent stack's own output -- session-down (QNT-459) reads this to
# check for the reaper's S3 marker without duplicating the bucket-naming convention
# (data_bucket.tf) in Python.
output "data_bucket_name" {
  value = data.terraform_remote_state.persistent.outputs.data_bucket_name
}
