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

# No defaults, mirroring image_tag -- both are per-session identity that must be supplied
# explicitly at apply time (session_up.py), never silently reused from a prior apply.
variable "session_id" {
  description = "This session's id (scripts/session_up.py) -- names the per-session reaper schedule and is passed to the reaper Lambda as its target input."
  type        = string
}

variable "session_start" {
  description = "This session's start time, RFC3339 UTC (matches the manifest's own `start` field) -- the reaper schedule fires `max_session_hours` after this."
  type        = string
}
