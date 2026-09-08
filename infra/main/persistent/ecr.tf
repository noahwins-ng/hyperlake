# QNT-457: the ingester image repo lives in persistent, not ephemeral -- rebuilding/pushing
# on every session would slow session-up and contradicts "ephemeral = compute only". Pushed
# by .github/workflows/ingester-image.yml (OIDC), tagged with the commit SHA; that SHA is
# the `deployed_sha` identity the ephemeral Fargate task definition is pinned to.
resource "aws_ecr_repository" "ingester" {
  name = "hyperlake-ingester"
}
