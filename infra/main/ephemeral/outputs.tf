output "backfill_state_machine_arn" {
  value = aws_sfn_state_machine.backfill.arn
}

output "kinesis_stream_name" {
  value = aws_kinesis_stream.trades.name
}
