# KYB Due-Diligence Agent (UK)

Enter a UK company number and get a **structured, cited risk report** that a compliance
analyst reviews and approves: the Know-Your-Business check fintechs run before onboarding
a business customer.

**Status:** 🚧 Week 2 of 3 toward a deployed v1: reports work end to end in the web app, API, CLI and MCP. See [docs/PLAN.md](docs/PLAN.md).

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

Run the web app (sign in with the local dev login, then search for a company):

```bash
make api        # terminal 1: API on :8000
make web        # terminal 2: web app on :3000 (copy web/.env.example to web/.env.local first)
```

Or generate a report from the command line, or use the API directly:

```bash
cd backend && uv run ukco report 13235004    # → reports/13235004_<date>.md + .json
make api                                     # → http://localhost:8000/docs
```

| Endpoint | |
|---|---|
| `POST /reports` | Queue a report (`{"company_number": "…", "as_of": "YYYY-MM-DD"?}`) → 202 |
| `GET /reports/{id}/events` | Server-sent progress: queued → fetching → … → succeeded |
| `GET /reports/{id}` | Status, the full cited report, current review decisions |
| `POST /reports/{id}/reviews` | Confirm/dismiss a flag, approve/reject the report |
| `GET /reports/{id}/reviews` | Append-only audit trail (UPDATE/DELETE blocked by a DB trigger) |

## Assistants (MCP) and observability

- **MCP server:** `uv run ukco mcp` exposes `search_companies`, `get_risk_report`,
  `get_flag_evidence` and `officer_network` to Claude Desktop / Claude Code. Setup: [docs/mcp.md](docs/mcp.md).
- **Tracing:** OpenTelemetry, exported to Langfuse (or any OTLP backend) when keys are set. One
  trace per report, with a span per stage and the LLM call as a generation (model, tokens,
  cost). Every report also records its own stage timings, Companies House request count and
  trace id. Prompt content isn't exported unless `TRACE_CONTENT=true` (it names officers).

## Eval results

Scored **as of 30 Sep 2024** using only what was known then; outcome = compulsory strike-off
or insolvency in the next 18 months. 60 real companies, frozen snapshot, run on every PR.
Method and caveats: [evals/README.md](evals/README.md).

| Metric | AUC [95% CI] |
|---|---|
| Risk score vs adverse outcome | **0.68** [0.55, 0.79] |
| …without filing-deadline flags | 0.61 [0.49, 0.72] |
| …insolvency vs survived only | 0.63 [0.44, 0.81] |

Baseline takeaway: a weak but real signal. The v1 rules catch distress that is already
visible (net liabilities, office churn) more than distress that is coming. That's the
target for the next iteration, measured against this baseline.
