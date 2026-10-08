# ADR-005: Lean single-server hosting on AWS for v1

- **Status:** Accepted
- **Date:** 2026-10-06
- **Amends:** ADR-001 (ECS Fargate + ALB + RDS becomes the documented scale-up path)

## Context
v1 is a portfolio demo: a handful of analysts, tens of reports a day. The original plan
(ECS Fargate, ALB, RDS) costs roughly **$45–55/month** before any traffic, with the ALB
(~$18) and RDS (~$15) as fixed costs. New AWS accounts get a limited credit allowance
(Free plan), and the owner wants AWS + Terraform experience without an ongoing bill.

## Decision
One ARM EC2 instance (`t4g.micro`, resizable by a Terraform variable) runs the
**same Docker image** CI builds, via `deploy/compose.yml`:

| Piece | Choice | Why |
|---|---|---|
| HTTPS | Caddy + Let's Encrypt on the box | No ALB/ACM cost; auto-renewing certs |
| Hostname | `<elastic-ip>.sslip.io` until a domain is bought | Real HTTPS with no domain needed |
| Database | Postgres 17 + pgvector in a container, data on encrypted EBS | No RDS cost; nightly `pg_dump` → S3 (versioned, 30-day expiry) |
| Access | SSM Session Manager only; security group opens 80/443 | No SSH keys, no port 22 |
| Secrets | SSM Parameter Store (SecureString) → `deploy/.env` at deploy time | Free tier; never in git or the image |
| Deploy | GitHub Actions (OIDC role, no stored AWS keys) → ECR → SSM Run Command: pull, migrate, restart | Same pipeline shape as ECS |
| Frontend | Vercel (Hobby) | Free; Next.js native |
| Guards | `check_production` refuses to start without secrets; daily per-user and site-wide report/chat limits; AWS budget alarm | A public demo can't run up a bill |

Rough cost: instance ~$7, public IPv4 ~$3.65, 20 GB gp3 ~$1.60, S3/ECR/logs < $1 → **about
$12–15/month**, covered by sign-up credits while they last.

## Consequences
- **Single point of failure:** a reboot means ~1 minute of downtime; a lost instance means
  restoring last night's dump (RPO 24 h). Acceptable for a demo, stated plainly in the README.
- **No horizontal scaling.** Report jobs run in-process, as before.
- **Scale-up path (not built):** the image and env contract are unchanged, so moving to ECS
  Fargate + ALB + RDS is a Terraform change, not an application change. Triggers: >1 instance
  needed, an uptime SLO, or managed backups/PITR required.
