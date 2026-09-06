# One bucket, prefix-separated by medallion layer (bronze/silver/gold/errors/) plus
# athena-results/ for query output. Prefixes are virtual in S3 — no placeholder objects
# needed; they come into existence the first time something is written under them.
resource "aws_s3_bucket" "data" {
  bucket = "hyperlake-data-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# athena-results/ is disposable query-result cache, not data — the only prefix with a
# lifecycle rule (ticket scope: "lifecycle rule only on athena-results/").
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }
}
