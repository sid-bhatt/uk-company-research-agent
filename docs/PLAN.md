# Delivery plan: KYB Due-Diligence Agent

**v1 (end of week 3):** one UK company number in → a full, cited, schema-validated risk report plus a review UI, deployed on AWS.
Budget: ~3–4 h/day, 6 days/week, 7th day is buffer. Product decision: [ADR-004](adr/0004-pivot-to-kyb-due-diligence.md).

**Cut rule:** if behind at end of Day 12, the "why flagged?" chat moves to week 4 and auth becomes shared demo + quota. The deploy date does not move.

## Done
| Day | Delivered |
|---|---|
| 1 | Repo, uv/ruff/pyright, pre-commit + gitleaks, compose (pgvector), CI, ADR-001 |
| 2 | Companies House client: rate limiter, retries, disk cache, input validation, recorded fixtures |
| 3 | Schema + Alembic, idempotent upserts, `ingest metadata`, seed list, `doctor`; found image-only PDFs (ADR-003) |

## Week 1 (rest): register data for one company, end to end
| Day | Tasks | Done |
|---|---|---|
| 4 | Pivot docs (ADR-004, data model, API map). Client: PSCs, PSC statements, officer appointments, charges, insolvency, advanced search. Fixtures + tests. | ☐ |
| 5 | Schema v2 migration: `officers` → `appointments` + `officers` (people), `pscs`, `psc_statements`, `source_snapshots`, stub companies, profile deadline fields. `ukco fetch <number>`: one company's full bundle + officer network (capped), every response snapshotted. | ☐ |
| 6 | Rules engine with `as_of`: overdue accounts/confirmation statement (computed deadlines), director churn, PSC changes, office changes, new company + high-risk SIC, officer network. `risk_policy.yaml`. Unit tests per rule on real fixtures. | ☐ |

## Week 2: financials, report, API, evals v0
| Day | Tasks | Done |
|---|---|---|
| 7 | iXBRL parser → `financial_facts` (cash, net assets, current assets/liabilities, turnover, employees; current + prior year from one filing). Financial-deterioration flag. | ☐ |
| 8 | `RiskReport` Pydantic schema, scoring, report assembler, citation validator. LLM summary + per-flag explanations via structured output, citing only given evidence IDs. `ukco report <number>` → JSON + Markdown. | ☐ |
| 9 | FastAPI: `POST /reports` (background job + SSE progress), `GET /reports/{id}`, `POST /reviews` (append-only audit). Persist reports/flags. Dockerfile. | ☐ |
| 10 | Eval set v0: 30 companies from advanced search (healthy + known outcomes), frozen snapshot, runner: rule fidelity vs hand labels, point-in-time outcome metrics, cost/latency. Baseline in README. | ☐ |
| 11 | MCP server (tools: `get_risk_report`, `get_flag_evidence`, `search_companies`, `officer_network`). Langfuse tracing on LLM calls. Buffer. | ☐ |
| 12 | Next.js scaffold, Auth.js (GitHub), report page: snapshot, score breakdown, flags by severity, citation links to Companies House / document page. | ☐ |

## Week 3: review UI + AWS
| Day | Tasks | Done |
|---|---|---|
| 13 | Review UI: confirm/dismiss per flag + note, approve/reject report, audit-trail view. | ☐ |
| 14 | "Why is this flagged?" chat drawer: agent with tools over the report's evidence (**cut candidate**). Buffer. | ☐ |
| 15 | Terraform bootstrap (state, GitHub OIDC, budget as code), network (no NAT), ECR, RDS. | ☐ |
| 16 | Terraform: ECS Fargate service, ALB, ACM, Route 53, Secrets Manager, logs. | ☐ |
| 17 | Deploy workflow (build → ECR → migrate → ECS). Frontend on Vercel. Per-user + global report quotas. | ☐ |
| 18 | Smoke test on 5 real companies, README v0 (architecture diagram, sample report screenshot, eval baseline, trade-offs), demo GIF. **Ship.** | ☐ |

## Weeks 4–6: evals + observability + pipeline
- Eval set to ~100 companies; **evals on every PR** (frozen snapshot, no live API), results table posted as PR comment + README.
- Financial-extraction accuracy vs hand-checked figures; PDF-only fallback (OCR balance-sheet pages + LLM extraction, [ADR-003](adr/0003-ocr-for-filing-documents.md)).
- Langfuse dashboards: cost and latency per report.
- Airflow: watchlist monitoring DAG (nightly re-run, alert on new flags) + eval-snapshot refresh DAG.

## Weeks 6–8: one paper-backed technique, measured
Candidates (pick one, measure against the week-6 eval baseline):
1. **Entity resolution** for disqualified-director matching (name + partial DOB + address), measuring match precision/recall, then its effect on outcome metrics.
2. **Graph features over the director network** (e.g. risk propagation from linked failed companies), measuring AUC lift.
3. **LLM-as-judge** for explanation faithfulness, calibrated against human ratings.

## Decisions (current)
| # | Decision | Choice |
|---|---|---|
| 1 | Product | KYB due-diligence report + review UI ([ADR-004](adr/0004-pivot-to-kyb-due-diligence.md)) |
| 2 | Rules vs LLM | Rules for anything computable; LLM for explanations, summaries, PDF extraction |
| 3 | Financials | iXBRL first; OCR + LLM fallback post-v1 |
| 4 | Evals | Rule fidelity + point-in-time predictive validity + extraction/citation accuracy; frozen snapshots in CI |
| 5 | LLM access | Anthropic API behind a thin client; Bedrock option later |
| 6 | Risk policy | `risk_policy.yaml`, versioned; shipped values are illustrative |
| 7 | Domain | Buy before Day 16 (HTTPS on ALB) |
| 8 | AWS budget | $50/month, alerts at 50/80/100% |
| 9 | Airflow | Watchlist monitoring + snapshot refresh; local Docker, prod as scheduled ECS task |
| 10 | Repo visibility | Private until v1, then public |
