# test-v1: every run of this held-out set

| Date (UTC) | Commit | Policy | AUC | Reason |
|---|---|---|---|---|
| 2026-11-13 13:15 | e97f0cc | v3 | 0.566 | Pre-tuning baseline: current rules (policy v3), before any week-4 changes. Week-4 rules are developed on v0 only. |
| 2026-11-14 12:02 | 6edaecc | v4 | 0.66 | Policy v4 (filing_history, 24-month director churn, dev-v1-fitted score weights): the one planned held-out measurement |

Commit ids were rewritten on 2026-11-25, when personal data was removed from the git
history before the repository went public. Each run above now points to the merged commit
of the pull request it was run from.
