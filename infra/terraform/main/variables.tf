variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "github_oidc_subject_prefix" {
  description = <<-EOT
    The repository part of GitHub's OIDC subject claim; only its main branch may deploy.
    This repo uses GitHub's immutable subject format (owner and repo IDs, so a deleted and
    re-created repo with the same name can't deploy). Check with:
      gh api repos/<owner>/<repo>/actions/oidc/customization/sub   (sub_claim_prefix)
  EOT
  type        = string
  default     = "repo:sid-bhatt@70953578/uk-company-research-agent@1472109773"
}

variable "instance_type" {
  description = "ARM (Graviton) instance; t4g.small if 1 GB proves tight."
  type        = string
  default     = "t4g.micro"
}

variable "alert_email" {
  description = "Where budget alerts go."
  type        = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 20
}

variable "api_host" {
  description = "Public hostname for the API. Empty = <elastic-ip>.sslip.io."
  type        = string
  default     = ""
}
