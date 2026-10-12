locals {
  api_host = var.api_host != "" ? var.api_host : "${replace(aws_eip.api.public_ip, ".", "-")}.sslip.io"
}

output "api_url" {
  value = "https://${local.api_host}"
}

output "api_host" {
  value = local.api_host
}

output "instance_id" {
  value = aws_instance.api.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ops_bucket" {
  value = aws_s3_bucket.ops.bucket
}

output "deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN variable in GitHub (not a secret)."
  value       = aws_iam_role.deploy.arn
}
