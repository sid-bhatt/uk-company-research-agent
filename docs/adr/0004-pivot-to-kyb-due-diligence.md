# ADR-004: Pivot to a KYB due-diligence agent

- **Status:** Accepted
- **Date:** 2026-09-29
- **Amends:** ADR-003 (OCR becomes a fallback, not the main path)

## Context
"Chat with Companies House documents" is a generic RAG demo. Fintech KYB/compliance teams
(Revolut, Stripe, Airwallex, Wise) have a concrete workflow: given a company, produce a risk
assessment an analyst can review, approve and defend to an auditor.

**Product:** a UK company number goes in; out comes a **structured, cited risk report** that
an analyst reviews flag by flag. Chat ("why is this flagged?") is secondary.

## Facts measured before deciding (2026-09-29, live API)
| Question | Finding | Consequence |
|---|---|---|
| Do small companies file machine-readable accounts? | **15/15** sampled 2021 incorporations' latest accounts offered iXBRL (micro-entity, abridged, dormant, total-exemption) | Financial figures for KYB-typical companies come from **tagged data, not OCR or an LLM** |
| Do large companies? | 1/470 FTSE/notable accounts offered iXBRL; PDFs are page images (ADR-003) | OCR + LLM extraction is the **fallback** for PDF-only filers |
| Can we get real outcomes for evals? | Advanced search: 6,840 `ltd` companies incorporated on 1–2 Mar 2021; **45%** of a sample already dissolved/in liquidation | Labelled eval population with ground-truth outcomes |
| Network check cost? | `/officers/{id}/appointments` returns each appointment's `appointed_to.company_status` | **One call per officer**, not per linked company |
| Overdue filings? | Profile has `accounts.overdue`, `confirmation_statement.overdue`, `registered_office_is_in_dispute`, `has_been_liquidated` | Live-mode checks are trivial (but see point-in-time below) |
| Office changes? | `AD01` filings carry `old_address`, `new_address`, `change_date` | Office history derivable from filing history |
| Disqualified directors? | Separate IDs, **not linked** to officer IDs; matching needs name + birth month/year | Entity resolution with false-positive risk → deferred to weeks 6–8 |
| Listed companies' PSCs? | Greggs: none (exempt under DTR5) | "No PSC" ≠ red flag for listed companies; rules must know company type |

## Decisions

### 1. Deterministic rules first, LLM only where it adds value
| Component | How |
|---|---|
| Overdue filings, director churn, PSC changes, office changes, new company + high-risk SIC, officer network | **Rules** over normalised tables. No LLM. |
| Financial figures (cash, net assets, turnover…) | **iXBRL tag parsing** (deterministic); LLM + OCR only for PDF-only filers (post-v1) |
| Summary, per-flag explanation, analyst follow-ups | **LLM**, structured output, may only cite evidence IDs it was given |
| Risk score | Transparent weighted sum from `risk_policy.yaml`, capped at 100. Every point traceable to a flag. |

Thresholds and the high-risk SIC list live in a versioned policy file, not code, so a
compliance team could change policy without a deploy. The shipped policy is **illustrative**.

### 2. Rules evaluate "as of" a date
Every rule takes `as_of` and only uses records dated on or before it (appointments, resignations,
PSC notifications, filings). A live report is `as_of = today`. This one design choice makes
honest back-testing possible (below) and makes reports reproducible.
Consequence: "overdue" is **computed** from filing deadlines, not read from the profile's
live boolean, which is used only as a cross-check.

### 3. Evidence is immutable
Every API response used in a report is stored in `source_snapshots` (body + sha256 + fetched_at).
A citation is `snapshot_id + JSON pointer`, or `document_id + page/element`. The analyst and
an auditor can always see exactly what the system saw, even after Companies House data changes.

### 4. Human-in-the-loop with an append-only audit trail
Analysts confirm/dismiss each flag (optional note) and approve/reject the report.
`reviews` is append-only: who, what, when, plus `rules_version` (git sha + policy hash),
`model_id` and `prompt_version` of the report being reviewed.

### 5. Evals: measure what isn't tautological
Pushback on "flag precision/recall": for deterministic flags, labels computed from the same
data just re-run the rule. We measure three things instead:

| Eval | What it proves | Labels |
|---|---|---|
| **Rule fidelity** | Rules read the register the way a human analyst does (same-day re-appointments, secretaries vs directors, corporate officers…) | ~30 companies hand-labelled per flag type by reading the Companies House page |
| **Predictive validity** (headline) | The risk score predicts real bad outcomes | Point-in-time: report `as_of = T`; outcome = compulsory strike-off notice (`GAZ1`, not voluntary `DS01`), liquidation or administration within 18 months. Metrics: AUC, precision@k |
| **LLM components** | Extraction and explanations are correct and grounded | Financial extraction vs hand-checked figures; citation resolvability/accuracy; explanation faithfulness (LLM-as-judge in weeks 6–8) |

Caveat in the README: Companies House strikes companies off *for* not filing, so "overdue"
predicting compulsory strike-off is partly mechanical. Report the metrics per outcome type,
and separately for insolvency (liquidation/administration), which is not mechanical.

CI never calls the live API: evals run on a **frozen snapshot** of the labelled companies
(reproducible; no rate-limit or data drift between PRs).

### 6. Personal data and reputational care
Officer data is public-register data, but a risk tool profiles real people and real companies.
Reports frame flags at company level; the public demo sits behind auth, is labelled
"demo, not advice", and the README states the lawful-basis assumptions a real deployment
would need (UK GDPR legitimate interests for KYB).

## What changes vs the original plan
| Area | Before | After |
|---|---|---|
| Output | Chat answer with citations | Pydantic-validated report + review UI; chat secondary |
| Corpus | 138 FTSE/notable companies, bulk OCR of 470 annual reports | On-demand per company + ~100 labelled eval companies (SME-heavy); FTSE kept as healthy controls |
| Financials | OCR everything (84k pages) | iXBRL first; OCR + LLM only for PDF-only filers, only balance-sheet pages (post-v1) |
| Airflow | Bulk ingestion | **Watchlist monitoring**: nightly re-run of reports for watched companies, alert on new flags ("perpetual KYB") |
| pgvector | Core retrieval | Supports chat follow-ups and PDF evidence search; off the v1 critical path |
| Evals | 100 Q&A golden set | Rule fidelity + point-in-time predictive validity + extraction/citation accuracy |
