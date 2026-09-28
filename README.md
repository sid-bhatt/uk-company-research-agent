# UK Company Research Agent

Ask questions about UK companies in plain English and get answers backed by official
Companies House records, with a citation for every claim.

> "Who are the current directors of Tesco PLC, and when were they appointed?"
> "What was Greggs' turnover in its last three annual accounts? Cite the pages."
> "Which directors sit on the boards of both company X and company Y?"

**Status:** 🚧 Week 1 of 3 toward a deployed v1. See [docs/PLAN.md](docs/PLAN.md).

## What it does

Every UK company must file public records with **Companies House**: who runs it,
who owns it, and yearly accounts, often as long PDFs. The data is free but slow to
dig through. This project:

1. **Collects** company records and filing documents from the Companies House API on a schedule.
2. **Reads and indexes** the PDFs so the relevant paragraph can be found in milliseconds.
3. **Answers questions** with an AI agent that decides which lookups to run (search a company,
   list its officers, fetch its filings, search inside documents) and writes an answer.
4. **Cites every claim** with a link to the exact filing and page, so a person can check it.
5. **Measures its own accuracy** against a set of ~100 questions with known answers, automatically on every code change.

## Architecture

_Diagram lands in week 3._ Planned components:

| Layer | Tech |
|---|---|
| Ingestion pipeline | Python package, orchestrated by Airflow (Docker) |
| Storage + search | Postgres + pgvector (semantic) + full-text search |
| Agent | LLM tool calling; same tools exposed as an MCP server |
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
make setup     # install deps + git hooks, create .env
make up        # start Postgres + pgvector
make doctor    # verify everything is wired up
make test
```

## Eval results

_Coming in weeks 4–6._
