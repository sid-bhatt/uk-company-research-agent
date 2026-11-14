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

**Frozen snapshots.** `data/<set>/snapshot.jsonl.gz` holds every API response the eval needs,
recorded once. CI replays it offline through the real client, parsers and rules: no API calls,
no rate limit, no drift. People's names and addresses are pseudonymised (a fresh mapping per
company), charge and filing free-text is dropped, and accounts are stored as extracted figures rather than
documents (filed accounts name the directors). A test scans the snapshot for personal data.

### Development and held-out sets

| Set | Companies | Role |
|---|---|---|
| `dev-v1` | 400 (seed 23, built 2026-11-14, no overlap with the others): 134 compulsory strike-off, 80 insolvency, 53 voluntary closure, 133 survived | **Development.** New rules and weights are built against it: big enough that a real signal stands out from noise (AUC intervals about ±0.05, against ±0.12 on v0). |
| `v0` | 60 (seed 7) | **Original set, now a regression check.** CI runs it on every PR (`--check` gates on AUC). The v1 rules were shaped while looking at it, so its numbers are optimistic. |
| `test-v1` | 100 (seed 11, built 2026-11-13, no overlap with v0), same population, quotas scaled: 34 compulsory strike-off, 20 insolvency, 13 voluntary closure, 33 survived | **Held out.** Measures finished rules. `ukco eval run --name test-v1` needs `--reason`, logs every run to [`results/test-v1/access_log.md`](results/test-v1/access_log.md), and is never a baseline or CI gate. |

```bash
uv run ukco eval build --name test-v1 --seed 11 --size 100 --exclude v0   # how it was built (once)
uv run ukco eval run --name test-v1 --reason "…"                           # a logged, deliberate look
```

### Current rules on the development set (policy v3)

| Metric | AUC [95% CI] | n |
|---|---|---|
| Risk score vs adverse outcome | **0.587** [0.536, 0.636] | 400 (214 adverse) |
| …without filing-deadline flags | 0.529 [0.481, 0.577] | 400 |
| …insolvency vs survived only | 0.612 [0.536, 0.688] | 213 (80 adverse) |

| Flag | Flagged | Caught (of adverse) | False alarms (of the rest) |
|---|---:|---:|---:|
| net_liabilities | 72 | 18% | 18% |
| psc_changes | 41 | 14% | 6% |
| overdue_confirmation_statement | 23 | 11% | 0% |
| office_changes | 34 | 9% | 8% |
| overdue_accounts | 20 | 9% | 1% |
| financial_deterioration | 31 | 8% | 8% |
| director_churn | 11 | 5% | 1% |

It agrees with the held-out set (0.566), so **v0's 0.68 was the outlier**. The points are
mis-set: `net_liabilities` (15 points) flags failed and healthy companies at the same rate,
while the overdue flags, the most precise, carry the fewest points. Several signals that
looked strong on v0 (filings per year: 0.69) shrink on 400 companies (0.58): with 60
companies, after trying many ideas, noise looks like signal.

### Held-out baseline (policy v3, before any week-4 changes)

| Metric | AUC [95% CI] | n |
|---|---|---|
| Risk score vs adverse outcome | **0.566** [0.466, 0.666] | 100 (54 adverse) |
| …without filing-deadline flags | 0.495 [0.405, 0.588] | 100 |
| …insolvency vs survived only | 0.617 [0.477, 0.759] | 53 (20 adverse) |

| Flag | Flagged | Caught (of adverse) | False alarms (of the rest) |
|---|---:|---:|---:|
| overdue_confirmation_statement | 8 | 13% | 2% |
| net_liabilities | 16 | 11% | 22% |
| overdue_accounts | 7 | 9% | 4% |
| financial_deterioration | 7 | 7% | 7% |
| office_changes | 6 | 7% | 4% |
| psc_changes | 4 | 7% | 0% |
| director_churn | 2 | 2% | 2% |

**What it says:** v0's 0.68 didn't generalise. On fresh companies the interval includes 0.5;
without the filing-deadline flags there's no signal. `net_liabilities` reverses (more false
alarms than catches), so it shouldn't carry the weight v0 suggested. This is the number week 4
has to beat, on this set, measured once.

### Results on v0 (development baseline, policy v1)

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
