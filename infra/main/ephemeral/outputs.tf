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
