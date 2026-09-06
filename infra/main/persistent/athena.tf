# Per-query bytes-scanned cap keeps a stray unfiltered query from becoming a bill
# surprise (NFR-2). 10 GB is far above any expected query at watchlist scale.
resource "aws_athena_workgroup" "hyperlake" {
  name = "hyperlake"

  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 10 * 1024 * 1024 * 1024

    result_configuration {
      output_location = "s3://${aws_s3_bucket.data.bucket}/athena-results/"
    }
  }
}
