# Smoke test: v1 on the live system (2026-10-19)

Run with [`backend/scripts/smoke_live.py`](../backend/scripts/smoke_live.py) against the
deployed API, the way the web app calls it: search, take the first hit (what pressing Enter
does), run the report, follow its progress events, check the stored result. Authenticated
with a short-lived token signed like the web app's.

## Run 1: deployed `main` (6106d62)

| Searched | Company | Status | Report | Time | Score | Flags | Not assessed | Evidence | CH calls |
|---|---|---|---|---|---|---|---|---|---|
| `greggs` | GREGGS PLC (00502851) | active | succeeded | 1.5 s | 15 | director_churn (medium) | financial_deterioration | 7 | 20 |
| `13235004` | ITEXP LTD (13235004) | active | succeeded | 0.9 s | 15 | financial_deterioration (medium) | none | 23 | 12 |
| `ikea limited` | IKEA LIMITED (01986283) | active | succeeded | 1.3 s | 30 | director_churn (high) | financial_deterioration | 8 | 20 |
| `temu payments` | TEMU PAYMENTS UK LIMITED (14476643) | active | succeeded | 0.6 s | 0 | none | financial_deterioration | 3 | 10 |
| `wilko limited` | WILKO LIMITED (09563205) | active | succeeded | 0.9 s | 5 | psc_changes (low) | none | 19 | 12 |
| `00365335` | WL REALISATIONS (2023) LIMITED (00365335) | **liquidation** | succeeded | 1.4 s | **0** | **none** | overdue_accounts, overdue_confirmation_statement, financial_deterioration | 3 | 14 |

All reports succeeded and passed citation validation, each in under 2 seconds.

## What it found

1. **A company in liquidation scored 0 (critical, fixed).** Searching "wilko limited" finds
   the *active* company that now holds the name (09563205); the Wilko that collapsed in 2023
   was renamed WL Realisations (2023) Ltd (00365335) and is in liquidation. Its report had no
   flags: the deadline rules correctly stand down for insolvent companies, and no rule looked
   at the company's own status. Fix: a `company_status` rule (policy v2). Insolvent or closed
   is a **knockout** (score 100); being struck off is a high-severity flag. Back-tests use dated
   filings only, so the outcome eval is unchanged (AUC 0.681): its companies were all active
   on the scoring date. Verified on the real data: 00365335 → score 100, F1 "Company is in
   liquidation"; the other five companies score as before.
2. **Search picked companies by an old name (fixed in #18).** "temu" first listed WOOLPEX LTD,
   formerly TEMU LTD. Results are now ranked by current name first and say what else matched.
   The Wilko case shows why that matters: the collapsed company only matches by its old name.
3. **Large companies' accounts aren't assessed.** Greggs, IKEA and Temu Payments file PDF or
   full accounts rather than small-company iXBRL, so financial deterioration is *not assessed*
   (shown as such, never as clear). PDF extraction is planned post-v1 (ADR-003).
4. **Known false positive:** director churn on large boards (Greggs medium, IKEA high) reflects
   normal rotation on big boards. Making churn relative to board size is in the plan.

## Run 2: after the fix is deployed

_To be filled in after this branch merges and deploys._
