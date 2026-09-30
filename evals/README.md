# Evaluation

Two questions, measured separately (design: [ADR-004](../docs/adr/0004-pivot-to-kyb-due-diligence.md)):

1. **Predictive validity:** scored *as of* a past date with only what was known then, does the
   risk score rank companies that went on to fail above those that didn't?
2. **Rule fidelity:** does each rule read the register the way a human analyst would?

## 1. Point-in-time outcome eval

| | |
|---|---|
| Eval date (T) | 30 Sep 2024 |
| Outcome window | 18 months (to 31 Mar 2026) |
| Population | Private limited companies incorporated 2020 to Sep 2023, at least 12 months old at T, not dissolved, gazetted, closing or insolvent by T |
| Adverse outcome | Compulsory strike-off notice (`GAZ1`) or insolvency other than a members' voluntary liquidation, within the window |
| Not adverse | Voluntary closure (`DS01` / `GAZ1(A)`), or survived |
| Sample | 60 companies, stratified: 20 compulsory strike-off, 12 insolvency, 8 voluntary closure, 20 survived |

Labels come from each company's own register records and cite them (filing transaction or
insolvency case). Sampling: random 3-day incorporation windows across three search sources;
every candidate is labelled, and kept only if eligible and its outcome's quota is open.

**Frozen snapshot.** `data/v0/snapshot.jsonl.gz` holds every API response the eval needs,
recorded once. CI replays it offline through the real client, parsers and rules: no API calls,
no rate limit, no drift. People's names and addresses are pseudonymised (a fresh mapping per
company), charge free-text is dropped, and accounts are stored as extracted figures rather than
documents (filed accounts name the directors). A test scans the snapshot for personal data.

### Results (baseline, policy v1)

| Metric | AUC [95% CI] | n |
|---|---|---|
| Risk score vs adverse outcome | **0.681** [0.554, 0.793] | 60 (32 adverse) |
| …without filing-deadline flags | 0.606 [0.487, 0.722] | 60 |
| …insolvency vs survived only | 0.631 [0.443, 0.812] | 32 (12 adverse) |

Mean score: adverse 8.6, not adverse 3.0.

| Flag | Flagged | Caught (of adverse) | False alarms (of the rest) |
|---|---:|---:|---:|
| net_liabilities | 10 | 22% | 11% |
| office_changes | 7 | 16% | 7% |
| overdue_accounts | 3 | 9% | 0% |
| financial_deterioration | 2 | 6% | 0% |
| overdue_confirmation_statement | 2 | 6% | 0% |
| psc_changes | 2 | 6% | 0% |
| director_churn | 1 | 0% | 4% |

Not assessed in back-tests: officer network (100%, by design: linked companies' statuses are
current), financial deterioration (20%: no accounts filed by T, or PDF-only).

### What the baseline says
- **Weak but real signal.** The interval clears 0.5, just.
- **The rules detect distress that is already visible, not distress that's coming.** Most
  companies struck off in the window weren't yet overdue at T, so deadline flags caught 9%.
- **Net liabilities is the most useful single signal** (22% caught vs 11% false alarms).
- Without the filing-deadline flags (which partly *cause* compulsory strike-off) the interval
  includes 0.5: no evidence yet of signal beyond the mechanical one.

Improvement candidates, each to be measured against this baseline:
- **leading indicators:** a history of late filing; persistent (not just current) net
  liabilities; dormant or no accounts in the second year
- **registered office moved to the Companies House default address** (`RP05`: the office was
  shown to be invalid), which the eval data surfaced
- more data: grow to ~100 companies so intervals narrow

**Caveats.** Stratified sample: AUC (rank-based) is valid, precision is not. Compulsory
strike-off is partly mechanical (not filing → strike-off). n = 60, so intervals are wide.

## 2. Rule fidelity (human-labelled)

`labels/rule_fidelity_v0.csv` lists, for 12 companies, what each rule concluded as of T.
A reviewer opens the Companies House link, checks the claim against the filing history, and
writes `agree` or `disagree` (plus a note). `ukco eval run` reports agreement per rule once
rows are labelled. **Status: awaiting labels.**

## Commands

```bash
cd backend
uv run ukco eval run                  # offline; writes results/v0/summary.json
uv run ukco eval run --check          # CI: fail if AUC drops > 0.05 below baseline.json
uv run ukco eval run --update-baseline
uv run ukco eval build --name v1      # re-sample from the live API (needs a key)
uv run ukco eval fidelity-sheet       # regenerate the labelling sheet
```
Every PR runs `ukco eval run --check` in CI and posts the table to the job summary.
