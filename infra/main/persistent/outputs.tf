output "data_bucket_name" {
  value = aws_s3_bucket.data.bucket
}

output "glue_database_name" {
  value = aws_glue_catalog_database.bronze.name
}

output "athena_workgroup_name" {
  value = aws_athena_workgroup.hyperlake.name
}
