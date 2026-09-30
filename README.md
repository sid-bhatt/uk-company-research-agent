# KYB Due-Diligence Agent (UK)

Enter a UK company number and get a **structured, cited risk report** that a compliance
analyst reviews and approves: the Know-Your-Business check fintechs run before onboarding
a business customer.

**Status:** 🚧 Week 2 of 3 toward a deployed v1: reports work end to end from the CLI (`ukco report <number>`). See [docs/PLAN.md](docs/PLAN.md).

## What the report contains
1. **Snapshot:** status, age, SIC codes, registered-office history, officers, people with significant control.
2. **Red flags**, each with severity, a one-line explanation, and a citation to the exact source
   (API record or filing page):
   overdue accounts / confirmation statement · director churn · officers linked to many
   failed companies · recent ownership (PSC) changes · frequent office moves ·
   very new company in a high-risk sector · financial deterioration in filed accounts.
3. **Risk score (0–100)** with every contributing reason.
4. **Review:** the analyst confirms or dismisses each flag; every decision goes into an audit trail.

**Design principle:** deterministic rules for everything computable from register data; the
LLM only where it adds value (explanations, summaries, reading PDF-only accounts).
Why and how: [ADR-004](docs/adr/0004-pivot-to-kyb-due-diligence.md).

**Evaluation:** rules are checked against hand-labelled companies, and the risk score is
back-tested *as of* a past date against what actually happened (compulsory strike-off,
liquidation, administration) in the following 18 months.

> Demo only, not financial or compliance advice. Uses public Companies House data.

## Architecture

_Diagram lands in week 3._ Data model: [docs/data-model.md](docs/data-model.md). API usage and rate budget: [docs/companies-house-api.md](docs/companies-house-api.md).

| Layer | Tech |
|---|---|
| Ingestion + monitoring | Python package; Airflow re-checks watchlisted companies nightly |
| Storage + search | Postgres + pgvector (semantic) + full-text search |
| Risk engine | Deterministic rules + versioned risk policy; LLM for explanations (structured output) |
| Agent | Analyst follow-up chat; tools also exposed as an MCP server |
| API | FastAPI, server-sent events for streaming |
| Frontend | Next.js / TypeScript, GitHub sign-in |
| Evals | Golden question set, run in GitHub Actions |
| Observability | Langfuse tracing: cost + latency per query |
| Infra | AWS (ECS Fargate, RDS, ECR) via Terraform |

Design decisions and trade-offs are recorded in [docs/adr/](docs/adr/).

## Run locally

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), a free
[Companies House API key](https://developer.company-information.service.gov.uk/).

```bash
make setup            # install deps + git hooks, create .env
make up               # start Postgres + pgvector
make migrate          # create the schema
make doctor           # verify DB, schema and a live API call
make ingest-metadata  # companies, officers, filing history for the seed list
make test
```

## Eval results

_Coming in weeks 4–6._
