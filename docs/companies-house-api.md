# Companies House API: endpoints and rate budget

Base: `https://api.company-information.service.gov.uk` (Document API: `https://document-api.company-information.service.gov.uk`).
Auth: HTTP Basic, REST API key as username. **Live** application keys only (test keys hit the sandbox).

## Endpoints

| Purpose | Endpoint | Used for | Client |
|---|---|---|---|
| Company profile | `GET /company/{n}` | Snapshot, status, age, SIC, overdue cross-check, `registered_office_is_in_dispute`, `previous_company_names` | ✅ |
| Officers | `GET /company/{n}/officers` | Current/former officers, churn | ✅ |
| Filing history | `GET /company/{n}/filing-history[?category=]` | Deadlines (computed overdue), `AD01` office changes, `GAZ1`/`DS01` outcomes, accounts documents | ✅ |
| Document metadata / content | `GET /document/{id}`, `/document/{id}/content` | iXBRL (`application/xhtml+xml`) or PDF | ✅ |
| Search | `GET /search/companies` | Company lookup by name | ✅ |
| PSCs | `GET /company/{n}/persons-with-significant-control` | Ownership snapshot, PSC changes (`notified_on`, `ceased_on`) | Day 4 |
| PSC statements | `GET /company/{n}/persons-with-significant-control-statements` | "No registrable PSC" / "PSC not identified" statements | Day 4 |
| Officer appointments | `GET /officers/{officer_id}/appointments` | **Network check**: every company the person is/was on, with `company_status`; `is_corporate_officer` | Day 4 |
| Charges | `GET /company/{n}/charges` | Outstanding charges (snapshot; later flag) | Day 4 |
| Insolvency | `GET /company/{n}/insolvency` (404 = none) | Insolvency cases | Day 4 |
| Advanced search | `GET /advanced-search/companies` (`incorporated_from/to`, `company_status`, `company_type`, `sic_codes`, `size`) | Building the labelled eval population | Day 4 |
| Disqualified officers | `GET /search/disqualified-officers`, `GET /disqualified-officers/{natural\|corporate}/{id}` | Disqualification match: **needs entity resolution** (IDs not linked to officers) | Weeks 6–8 |
| Streaming API | `stream.companieshouse.gov.uk` (separate streaming key) | Real-time change feed for watchlist monitoring | Phase 3 |
| Bulk accounts data | Free daily/monthly iXBRL zip downloads (not the REST API) | Financial back-fill without API calls | Optional |

## Rate limit
**600 requests per 5-minute window per key** (headers: `X-Ratelimit-Limit/Remain/Reset/Window`; confirmed live).
Our client runs at 550 per window, obeys `Remain`/`Reset`, and backs off on 429. Assume Document API calls count too.

### Budget per report
| Call | Small company | Large company |
|---|---|---|
| Profile, officers, PSCs, PSC statements, charges, insolvency | 6 | 6–8 |
| Filing history (10 years, 100/page) | 1 | 3–10 |
| Officer appointments (one per distinct officer in last 3 years; 50/page, **capped at 4 pages**) | 2–8 | 20–60 |
| Latest accounts: metadata + iXBRL | 2 | 2 (PDF: OCR path) |
| **Total** | **~10–20** | **~35–80** |

At 550 per window that's roughly **25–50 small-company reports every 5 minutes** uncached, which is fine for an analyst tool.

### Concerns and mitigations
| Concern | Mitigation |
|---|---|
| Professional/nominee directors with thousands of appointments (formation agents, corporate secretaries) | Cap at 200 appointments per officer and mark the network check `truncated`; exclude `is_corporate_officer` and secretaries from the dissolved-links flag |
| Evals: 100 companies × ~20 calls = ~2,000 calls (~20 min) and live data drifts | Fetch once into a **frozen snapshot**; CI never calls the live API |
| One key shared by API server, Airflow and eval builds | Separate keys per environment (local, CI snapshot builder, prod); the limit is per key |
| Stale evidence | Cache TTL 24h for company data, but every report stores its own `source_snapshots`, so the report is reproducible regardless of cache |
| Demo abuse (someone scripts 1,000 reports) | Per-user daily report quota + global quota in the API (week 3) |
