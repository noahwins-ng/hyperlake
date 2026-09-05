output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "state_lock_table" {
  value = aws_dynamodb_table.tflock.name
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}
