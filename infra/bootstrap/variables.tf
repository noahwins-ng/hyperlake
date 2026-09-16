variable "budget_notification_email" {
  description = "Email address for the $10 budget alarm and the $15 deny-action notice. No default on purpose, never commit an address to the repo."
  type        = string
  sensitive   = true
}

variable "github_repo" {
  description = "GitHub org/repo allowed to assume the OIDC role, as \"org/repo\"."
  type        = string
  default     = "noahwins-ng/hyperlake"
}
