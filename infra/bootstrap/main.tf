provider "aws" {
  region = "ap-northeast-1"

  default_tags {
    tags = {
      project = "hyperlake"
    }
  }
}

data "aws_caller_identity" "current" {}
