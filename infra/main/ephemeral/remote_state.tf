data "terraform_remote_state" "persistent" {
  backend = "s3"
  config = {
    bucket = var.tfstate_bucket
    key    = "persistent/terraform.tfstate"
    region = "ap-northeast-1"
  }
}

data "aws_caller_identity" "current" {}
