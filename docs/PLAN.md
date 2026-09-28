# Delivery plan

Budget: ~3–4 h/day, 6 days/week, 7th day is buffer.
**Cut rule:** if behind at end of week 2, replace auth with shared demo + IP rate limit + global spend cap. The deploy date does not move.

## Phase 1 — Deployed v1 (weeks 1–3)

### Week 1: data in, retrieval working locally
| Day | Tasks | Done |
|---|---|---|
| 1 | Secure the AWS account + **budget alarm** ([checklist](aws-day1-checklist.md)). Companies House API key. Repo scaffold: uv, ruff, pyright, pre-commit + gitleaks, compose (pgvector), CI. ADR-001. | ☐ |
| 2 | Companies House client: search, profile, officers, filing history, document metadata/content. Rate limiter (600 req / 5 min), retries, disk cache. Tests on recorded fixtures. | ☐ |
| 3 | Schema + Alembic: `companies`, `officers`, `filings`, `documents`, `chunks` (embedding, tsv, page, hash), `ingestion_runs`. Commit seed company list. | ☐ |
| 4 | Document download + parse (PyMuPDF), detect scanned PDFs → `skipped_no_text`, page-aware chunking. | ☐ |
| 5 | Embed + upsert with hash skip. `ukco ingest` end-to-end on 10 companies, then full seed. HNSW index. | ☐ |
| 6 | Retrieval function (vector + company filter). Manual sanity checks. **First 15 golden questions.** Buffer. | ☐ |

### Week 2: agent, API, MCP
| Day | Tasks | Done |
|---|---|---|
| 7 | Tool-calling loop, 4 tools (`search_companies`, `get_officers`, `list_filings`, `search_filings`). CLI chat. | ☐ |
| 8 | **Citation contract** (`[n]` → chunk id / API URL), "I don't know" behaviour, system prompt v1. | ☐ |
| 9 | FastAPI `/chat` over SSE, health check, Dockerfile, conversation persistence. | ☐ |
| 10 | MCP server over the shared tool registry, tested with MCP Inspector. Minimal Langfuse tracing. | ☐ |
| 11 | Small eval runner: 20 questions, deterministic metrics (recall@k, tool-call accuracy). Baseline recorded. | ☐ |
| 12 | Buffer. Next.js scaffold. 10 more golden questions. | ☐ |

### Week 3: frontend + AWS
| Day | Tasks | Done |
|---|---|---|
| 13 | Chat UI: streamed tokens, collapsible tool-call steps, citation chips linking to Companies House. | ☐ |
| 14 | Auth.js (GitHub) → JWT verified by FastAPI. Per-user quota + global daily spend cap. | ☐ |
| 15 | Terraform bootstrap (state bucket, GitHub OIDC role, budget as code). Network (no NAT), ECR, RDS + pgvector, secrets. | ☐ |
| 16 | Terraform: ECS cluster/service, ALB, ACM cert, Route 53, log groups. | ☐ |
| 17 | `deploy.yml`: build → ECR → migrate → ECS rollout. Load prod data via a one-off ECS task running `ukco ingest`. Frontend on Vercel. | ☐ |
| 18 | Smoke tests, README v0 (architecture diagram, limitations), 60 s demo GIF. **Ship.** | ☐ |

## Phase 2 — Evals + observability (weeks 4–6)
Golden set to ~100 questions. Faithfulness + citation accuracy (LLM judge). PR subset in CI with recorded API responses; full suite nightly. Langfuse dashboards, cost/latency per query. Hybrid retrieval (vector + full text, RRF), justified by eval deltas. Airflow DAGs wrapping `ukco.ingest`.

## Phase 3 — Pipeline hardening + polish
Incremental refresh (Companies House streaming API / filing-history diffs), backfills, data-quality checks, OCR stretch goal (Textract), README eval table and trade-offs.

## Open decisions (defaults applied until overridden)
| # | Decision | Default |
|---|---|---|
| 1 | Company corpus | FTSE 100 + ~50 well-known private/scale-ups; last 3 years of accounts, confirmation statements, officers |
| 2 | Hero questions | _To define by Day 6_ |
| 3 | LLM access | Anthropic API weeks 1–3 behind a thin client; Bedrock from week 4 |
| 4 | Embedding model | _Decide by Day 5 (ADR-002)_: locks vector dimension |
| 5 | Domain | Buy one before Day 16 (needed for HTTPS on ALB) |
| 6 | AWS budget | $50/month, alerts at 50/80/100% |
| 7 | Airflow in prod | Local Docker + CI-tested DAGs; prod refresh as scheduled ECS task |
| 8 | Scanned PDFs | Skip + count in v1 |
| 9 | Repo visibility | Private until Day 1 CI is green, then public |
