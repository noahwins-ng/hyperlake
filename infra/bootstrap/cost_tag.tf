# AWS only lists a tag as activatable once it has appeared in billing data,
# which lags actual resource tagging by up to 24h (FR-7). On a first-ever
# apply this resource can fail with the tag not yet found — re-run
# `terraform apply` the next day; see docs/guides/bootstrap.md.
resource "aws_ce_cost_allocation_tag" "project" {
  tag_key = "project"
  status  = "Active"
}
