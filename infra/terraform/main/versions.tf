terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  # Bucket comes from bootstrap: `make tf-init` passes -backend-config="bucket=…".
  backend "s3" {
    key          = "main/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "ukco", stack = "main" } }
}

data "aws_caller_identity" "me" {}
