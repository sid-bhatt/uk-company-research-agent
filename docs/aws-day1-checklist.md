# AWS account: Day 1 checklist (manual, ~30 min)

Do this in the AWS console **before** any Terraform. Everything here gets codified in `infra/terraform/bootstrap` on Day 16, except the root account steps.

- [ ] **Root user MFA** on (IAM → Security credentials). Then stop using root.
- [ ] **No root access keys.** Delete any that exist.
- [ ] **IAM Identity Center** enabled in `eu-north-1`; create your own user with the `AdministratorAccess` permission set. Log in through the access portal from now on.
- [ ] **AWS CLI SSO profile:** `aws configure sso` → profile name `ukco`. Test: `aws sts get-caller-identity --profile ukco`.
- [ ] **Billing → Budgets → Create budget**: monthly cost budget **$20**, email alerts at **50%, 80%, 100% actual** and **100% forecasted**.
- [ ] **Billing preferences:** enable "Receive Free Tier usage alerts" and "Receive CloudWatch billing alerts".
- [ ] **Cost Anomaly Detection:** create the default monitor with email alerts.
- [ ] Default region set to **eu-north-1 (Stockholm)**.
