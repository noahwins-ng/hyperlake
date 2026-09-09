terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Local state on purpose (NFR-7): this stack creates the S3 backend that
  # infra/main uses, so it can't depend on that backend existing yet.
}
