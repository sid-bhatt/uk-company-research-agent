# ADR-001: Architecture baseline

- **Status:** Accepted
- **Date:** 2026-09-28

## Context
Portfolio project with a 3-week deadline for a deployed v1, built at ~3–4 h/day by one engineer.
It must show production AI engineering skills: ingestion, retrieval, agents, evals,
observability, and cloud deployment. It must also stay cheap to run (target ≤ $50/month).

## Decisions

| Area | Decision | Rejected alternative | Why |
|---|---|---|---|
| Store | Postgres + pgvector for relational data, vectors and full-text search | Separate vector DB (Pinecone, Qdrant) | One system to run, back up and query; joins between filings and chunks; hybrid search is a SQL change. |
| Pipeline shape | Plain Python package + CLI; Airflow DAGs are thin wrappers (weeks 4–6) | Logic inside Airflow operators | Testable without Airflow; the same code runs from the CLI, ECS one-off tasks and Airflow. |
| Agent framework | Provider SDK with a hand-written tool loop | LangChain / LlamaIndex | Fewer abstractions, easier to trace and test. |
| Tool registry | One registry shared by the agent and the MCP server | Separate implementations | No drift between the two interfaces. |
| Backend compute | ECS Fargate behind an ALB | Lambda | Long SSE streams and multi-step agent loops; same container locally and in prod; no cold starts. |
| Frontend hosting | Vercel | Amplify / OpenNext on AWS | Next.js on AWS costs time with little learning value; AWS effort goes to the backend. |
| Networking | Public subnets with tight security groups, no NAT gateway | Private subnets + NAT | NAT costs ~$35/month; acceptable risk for a demo with no sensitive data. |
| Python tooling | uv, ruff, pyright, pytest | pip/poetry, flake8/black, mypy | Fast, current, single-tool lint and format. |

## Consequences
- Everything runs locally with `docker compose` + `uv`.
- Changing the embedding model later means re-embedding everything; see ADR-002 (Day 5).
- Tasks in public subnets must never hold secrets on disk; secrets come from Secrets Manager at runtime.
