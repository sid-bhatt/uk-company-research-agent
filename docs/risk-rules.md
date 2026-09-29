# KYB risk rules (v1)

Deterministic rules over Companies House register data (`backend/src/ukco/risk/rules.py`),
thresholds in [`risk_policy.yaml`](../backend/src/ukco/risk/risk_policy.yaml) (illustrative).
Every rule returns **flagged**, **clear** or **not assessed**, always with a reason, and every
flag cites the exact stored API records behind it.

**Live vs back-test.** An assessment runs *as of* a date. Live (as of the fetch date) can use
the register's own markers. A back-test only uses records dated on or before `as_of`; signals
that are only known "as of now" report *not assessed* instead of leaking the future.

| Rule | Data | Live | Back-test | Caveats |
|---|---|---|---|---|
| **Overdue accounts** | Profile `accounts`; `AA` filings' made-up dates | Register's `next_due` / `overdue` | Computed deadline (9 months private, 6 plc; first accounts 21/18 months) | Not modelled: ARD changes, extensions. Computed deadlines are tested against the register's (exact for the small-company fixture, ±7 days for Greggs' 52-week year) |
| **Overdue confirmation statement** | Profile `confirmation_statement`; `CS01`/`AR01` | Register | Computed (12 months + 14 days) | |
| **Director churn** | Company officer list (appointed/resigned dates) | ✓ | ✓ | Ignores the initial board (first 30 days) and same-day resign + re-appoint (role changes) |
| **PSC changes** | PSC register and statements | ✓ | ✓ | Listed companies are exempt from the PSC register |
| **Office changes** | `AD01` filings' change dates | ✓ + disputed / undeliverable markers | ✓ (markers not used: undated) | |
| **New company, higher-risk SIC** | Incorporation date, SIC codes | ✓ | ✓ | SIC codes are current, not historical |
| **Officer network** | Each director's appointment list with linked companies' statuses | ✓ | **Not assessed** | Linked statuses are current, so back-testing would leak outcomes. "Dissolved" alone needs volume and a high share (voluntary strike-off is common); insolvency statuses count on their own. Secretaries and corporate officers excluded |
| **Financial deterioration** | Filed accounts (iXBRL) | Day 7 | Day 7 | |

Rules not in v1, and why:
- **Disqualified directors:** disqualification records aren't linked to officer IDs, so matching
  needs entity resolution (name + partial birth date), which carries a false-accusation risk.
  This is the weeks 6–8 research candidate.
- **Outstanding charges:** fetched and stored; charges are normal for trading companies, so they
  need financial context before they mean anything.

**Score** = sum of flag points (low 5 / medium 15 / high 30), capped at 100. Every point is
traceable to a flag. The score is a triage aid, not a probability; its predictive validity is
what the Day 10 evals measure.
