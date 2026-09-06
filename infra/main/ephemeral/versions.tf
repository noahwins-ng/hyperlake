# Empty on purpose (QNT-450 scope: persistent layer only). Compute + streams land in a
# later ticket. This root exists now so the persistent stack's destroy-safety (AC4) is
# provable: destroying this root has nothing to destroy and never touches persistent's
# separate state.
terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
