variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "github_repo" {
  description = "owner/name of the repository allowed to deploy (main branch only)."
  type        = string
  default     = "sid-bhatt/uk-company-research-agent"
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
