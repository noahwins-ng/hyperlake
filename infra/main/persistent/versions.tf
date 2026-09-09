terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }

  # bucket + dynamodb_table come from infra/bootstrap's outputs and are supplied at init
  # time via -backend-config (shared with the ephemeral root; only the key differs). See
  # docs/guides/bootstrap.md.
  backend "s3" {
    key     = "persistent/terraform.tfstate"
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

data "aws_caller_identity" "current" {}
