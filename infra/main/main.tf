terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.62"
    }
  }

  # bucket + dynamodb_table are account-specific (embed the account ID) and
  # come from infra/bootstrap's outputs, so they're supplied at init time via
  # -backend-config rather than hardcoded here. See docs/guides/bootstrap.md.
  backend "s3" {
    key     = "main/terraform.tfstate"
    region  = "ap-northeast-1"
    encrypt = true
  }
}

provider "aws" {
  region = "ap-northeast-1"

  default_tags {
    tags = {
      project = "hyperlake"
    }
  }
}
