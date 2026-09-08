variable "tfstate_bucket" {
  description = "Terraform state bucket (infra/bootstrap output `state_bucket`) -- read here to look up the persistent stack's outputs via remote state, same value backend.hcl points the backend at."
  type        = string
}

# No default on purpose: forces an explicit commit SHA at apply time (e.g. `make
# tf-apply-ephemeral TF_ARGS="-var image_tag=$(git rev-parse HEAD)"`) rather than silently
# reusing whatever tag was last applied -- this is the `deployed_sha` identity AC4 checks.
variable "image_tag" {
  description = "Commit SHA the ingester image was pushed under (.github/workflows/ingester-image.yml) -- pins the running task's image tag."
  type        = string
}

variable "max_session_hours" {
  description = "Ingester self-exit bound, passed through as --max-session-hours."
  type        = number
  default     = 6
}
