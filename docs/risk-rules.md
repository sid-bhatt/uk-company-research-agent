# KYB risk rules (policy v5)

Deterministic rules over Companies House register data (`backend/src/ukco/risk/rules.py`),
thresholds in [`risk_policy.yaml`](../backend/src/ukco/risk/risk_policy.yaml) (illustrative).
Every rule returns **flagged**, **clear** or **not assessed**, always with a reason, and every
flag cites the exact stored API records behind it. One check can also return **noted** ("to
verify"): context for the analyst, with cited evidence, that is not a flag, adds no points and
needs no review decision.

**Live vs back-test.** An assessment runs *as of* a date. Live (as of the fetch date) can use
the register's own markers. A back-test only uses records dated on or before `as_of`; signals
that are only known "as of now" report *not assessed* instead of leaking the future.

| Rule | Data | Live | Back-test | Caveats |
|---|---|---|---|---|
| **Company status** *(knockout)* | Profile `company_status`; insolvency/closure filings (`LIQ*`, `AM*`, `COCOMP`, `GAZ2`, old `4.`/`2.` forms); strike-off filings (`DS01`, `GAZ1`) | Register status: liquidation, administration, receivership, voluntary arrangement, dissolved or closed → **knockout (score 100)**; "proposal to strike off" → high | Same, from dated filings only | Added after the v1 smoke test: a company in liquidation scored 0, because the deadline rules correctly stand down for it and nothing else looked at its status. A strike-off withdrawn by a later `DS02` doesn't count. **v5:** a restoration to the register (`RT01` administrative, `AC92` court order) supersedes every dissolution, closure or strike-off filed before it, so a restored company is no knockout; a restoration in the last 36 months is its own high flag ("struck off and restored", 30 points), because a company is usually struck off for not filing. Found live: Boundaryless Ltd was scored 100 for a 2024 dissolution undone by a restoration a month later |
| **Overdue accounts** | Profile `accounts`; `AA` filings' made-up dates | Register's `next_due` / `overdue` | Computed deadline (9 months private, 6 plc; first accounts 21/18 months) | Not modelled: ARD changes, extensions. Computed deadlines are tested against the register's (exact for the small-company fixture, ±7 days for Greggs' 52-week year) |
| **Overdue confirmation statement** | Profile `confirmation_statement`; `CS01`/`AR01` | Register | Computed (12 months + 14 days) | |
| **Filing history** *(v4)* | `AA`, `CS01`/`AR01` filings: filing date vs the deadline from each filing's own made-up date | ✓ | ✓ | Late filings in the last 24 months: 1 → low, 2–3 → medium, 4+ → high. Catches a *habit* of late filing that the overdue rules miss once the filing finally arrives. Accounts due 9 months after the period (plc 6); first accounts over 12 months due 21 (18) months after incorporation or 3 months after the period, whichever is later (s.442). Deadline extensions aren't modelled |
| **Director churn** | Company officer list (appointed/resigned dates) | ✓ | ✓ | 24 months: 1–2 changes → low, 3–4 → medium, 5+ → high (v3: 12 months, from 3). Ignores the initial board (first 30 days) and same-day resign + re-appoint (role changes) |
| **PSC changes** | PSC register and statements | ✓ | ✓ | Listed companies are exempt from the PSC register |
| **Office changes** | `AD01` filings' change dates | ✓ + disputed / undeliverable markers | ✓ (markers not used: undated) | |
| **New company, higher-risk SIC** | Incorporation date, SIC codes | ✓ | ✓ | SIC codes are current, not historical |
| **Officer network** | Each director's appointment list with linked companies' statuses | ✓ | **Not assessed** | Linked statuses are current, so back-testing would leak outcomes. "Dissolved" alone needs volume and a high share (voluntary strike-off is common); insolvency statuses count on their own. Secretaries and corporate officers excluded |
| **Financial deterioration** | Latest two iXBRL accounts filings (each carries prior-year comparatives) | ✓ | ✓ only filings filed by `as_of` | Flags a ≥50% fall in net assets / net current assets / current assets / cash from a base ≥ £10k, and net liabilities (high if net assets turned negative). PDF-only accounts (large/paper filers) → *not assessed* |
| **Financial substance** *(note, 0 points)* | Net assets in the latest iXBRL accounts; accounts type (dormant) | ✓ | ✓ only filings filed by `as_of` | **Noted** when net assets are £0–£1,000 (e.g. £100 of share capital, unchanged) or the accounts are dormant: verify the business trades as the customer describes. Not scored, because tiny net assets are normal for dormant, holding and owner-managed companies. In the v0 eval set, noted companies were *less* often adverse (8/17) than clear ones (20/31), many closing voluntarily, so scoring it would only add false alarms. Net liabilities are left to the deterioration check |

Rules not in v1, and why:
- **Disqualified directors:** disqualification records aren't linked to officer IDs, so matching
  needs entity resolution (name + partial birth date), which carries a false-accusation risk.
  This is the weeks 6–8 research candidate.
- **Outstanding charges:** fetched and stored; charges are normal for trading companies, so they
  need financial context before they mean anything.

**Financial extraction** (`ukco.financials`): stdlib iXBRL parser → canonical metrics, entity-level
facts only (dimensioned facts are breakdowns). Measured on 25 real March-2021 companies (50 filings):
all iXBRL; net assets present in 100% of filings, net current assets 84%, current assets 78%,
**cash 44%, turnover 2%** (micro-entities rarely disclose either), so net assets carry the signal.
Net assets equal total equity in every filing (sign/scale parsing correct). Some filings tag
creditors as *negative* (debit balances), so "net current assets ≤ current assets" does not hold for
real data; the parser reproduces the filing as filed, and figures reconcile within each filing.

**Score** = sum of flag points, capped at 100. A flag's points are its severity's points (low 5 /
medium 15 / high 30) times its rule's **score weight**, rounded. Every point is traceable to a
flag. The score is a triage aid, not a probability.

**Score weights (v4)** were fitted on the development set `dev-v1` only: a logistic regression
of "failed within 18 months" on each flag type's points, then rounded. The held-out set
`test-v1` played no part. Unlisted rules keep weight 1.

| Rule | Weight | Medium flag scores |
|---|---|---|
| Overdue confirmation statement | 2.2 | 33 |
| Overdue accounts | 1.6 | 24 |
| PSC changes | 1.1 | 16 |
| Filing history | 0.7 | 10 |
| Director churn | 0.4 | 6 |
| Office changes, net liabilities, financial deterioration | 0.3 (floor) | 4–5 |
| Company status, officer network, new company + higher-risk SIC, registered-office markers | 1 | 15 |

The three "floor" rules showed no link to failure in the eval data. They stay in the report in
full and still score lightly, because they bear on fraud and money-laundering risk, which a
failure label doesn't measure. Rules with weight 1 can't be measured by the back-test: they're
live-only, or no eval company was young enough to trigger them.

Results: [evals/README.md](../evals/README.md).
