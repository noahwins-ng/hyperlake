variable "tfstate_bucket" {
  description = "Terraform state bucket (infra/bootstrap output `state_bucket`) -- read here to look up the persistent stack's outputs via remote state, same value backend.hcl points the backend at."
  type        = string
}
