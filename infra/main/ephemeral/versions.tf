# First real resources land here in QNT-451 (backfill Lambda). Fargate/Kinesis/Firehose
# land in later tickets. This root exists separately from persistent/ so a session's
# `terraform destroy` here never touches the persistent stack's own state.
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
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  backend "s3" {
    key     = "ephemeral/terraform.tfstate"
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
