"""Run the outcome eval offline over a frozen snapshot and summarise it.

For each company: replay its bundle, score it as of T with only what was known then,
and compare against what actually happened in the next 18 months.
"""

import datetime as dt
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ukco.evals.build import AS_OF
from ukco.evals.frozen import read_snapshot, replay_bundle
from ukco.evals.labels import ADVERSE, WINDOW_MONTHS
from ukco.evals.metrics import auc, bootstrap_auc_ci, rate
from ukco.risk import assess, default_policy

# Flags driven by filing deadlines: Companies House strikes companies off *for* not filing,
# so these partly predict compulsory strike-off by construction. Reported separately.
MECHANICAL_FLAGS = {"overdue_accounts", "overdue_confirmation_statement"}


@dataclass
class CompanyResult:
    company_number: str
    outcome: str
    adverse: bool
    score: int
    score_without_deadlines: int
    flags: list[str]
    not_assessed: list[str]


async def evaluate(snapshot: Path, *, as_of: dt.date = AS_OF) -> list[CompanyResult]:
    results = []
    for company in read_snapshot(snapshot):
        bundle, evidence = await replay_bundle(company)
        a = assess(bundle, evidence, as_of=as_of)
        mechanical = sum(f.points for f in a.flags if f.flag_type in MECHANICAL_FLAGS)
        results.append(
            CompanyResult(
                company_number=company.company_number,
                outcome=company.outcome,
                adverse=company.outcome in ADVERSE,
                score=a.risk_score,
                score_without_deadlines=min(100, sum(f.points for f in a.flags) - mechanical),
                flags=sorted(f.flag_type for f in a.flags),
                not_assessed=sorted(o.rule for o in a.outcomes if o.status == "not_assessed"),
            )
        )
    return results


def summarise(results: list[CompanyResult], *, as_of: dt.date = AS_OF) -> dict[str, Any]:
    adverse = [r.adverse for r in results]
    scores = [float(r.score) for r in results]
    no_deadline = [float(r.score_without_deadlines) for r in results]
    insolvency_or_survived = [r for r in results if r.outcome in ("insolvency", "survived")]

    def auc_block(values: list[float], labels: list[bool]) -> dict[str, Any]:
        ci = bootstrap_auc_ci(values, labels)
        value = auc(values, labels)
        return {
            "auc": value and round(value, 3),
            "ci95": ci and [round(ci[0], 3), round(ci[1], 3)],
            "n": len(values),
            "positives": sum(labels),
        }

    n_pos, n_neg = sum(adverse), len(adverse) - sum(adverse)
    per_flag = {}
    for flag in sorted({f for r in results for f in r.flags}):
        hit = [r for r in results if flag in r.flags]
        pos = sum(r.adverse for r in hit)
        per_flag[flag] = {
            "flagged": len(hit),
            "recall": rate(pos, n_pos),  # share of adverse companies this flag caught
            "false_alarm_rate": rate(len(hit) - pos, n_neg),  # share of the rest it flagged
        }
    coverage = Counter(rule for r in results for rule in r.not_assessed)
    return {
        "as_of": str(as_of),
        "window_months": WINDOW_MONTHS,
        "policy_sha256": default_policy().sha256,
        "companies": len(results),
        "outcomes": dict(Counter(r.outcome for r in results)),
        "score_auc": auc_block(scores, adverse),
        "score_auc_without_deadline_flags": auc_block(no_deadline, adverse),
        "score_auc_insolvency_vs_survived": auc_block(
            [float(r.score) for r in insolvency_or_survived],
            [r.adverse for r in insolvency_or_survived],
        ),
        "mean_score": {
            "adverse": round(sum(r.score for r in results if r.adverse) / max(n_pos, 1), 1),
            "not_adverse": round(sum(r.score for r in results if not r.adverse) / max(n_neg, 1), 1),
        },
        "per_flag": per_flag,
        "not_assessed_share": {
            rule: round(k / len(results), 2) for rule, k in sorted(coverage.items())
        },
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def to_markdown(summary: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    def auc_line(label: str, key: str) -> str:
        block = summary[key]
        ci = block["ci95"]
        base = baseline.get(key, {}).get("auc") if baseline else None
        delta = (
            f" ({block['auc'] - base:+.3f} vs baseline)"
            if base is not None and block["auc"] is not None
            else ""
        )
        interval = f" [{ci[0]}, {ci[1]}]" if ci else ""
        n = f"{block['n']} ({block['positives']} adverse)"
        return f"| {label} | **{block['auc']}**{interval}{delta} | {n} |"

    lines = [
        f"### Outcome eval: scored as of {summary['as_of']}, "
        f"outcomes over the next {summary['window_months']} months",
        "",
        f"{summary['companies']} companies: "
        + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(summary["outcomes"].items())),
        "",
        "| Metric | AUC [95% CI] | n |",
        "|---|---|---|",
        auc_line("Risk score vs adverse outcome", "score_auc"),
        auc_line("…without filing-deadline flags", "score_auc_without_deadline_flags"),
        auc_line("…insolvency vs survived only", "score_auc_insolvency_vs_survived"),
        "",
        f"Mean score: adverse **{summary['mean_score']['adverse']}**, not adverse "
        f"**{summary['mean_score']['not_adverse']}**.",
        "",
        "| Flag | Flagged | Caught (of adverse) | False alarms (of the rest) |",
        "|---|---:|---:|---:|",
    ]
    for flag, s in summary["per_flag"].items():
        lines.append(
            f"| {flag} | {s['flagged']} | {_pct(s['recall'])} | {_pct(s['false_alarm_rate'])} |"
        )
    if summary["not_assessed_share"]:
        lines += [
            "",
            "Not assessed: "
            + ", ".join(f"{k} {v:.0%}" for k, v in summary["not_assessed_share"].items()),
        ]
    fidelity = summary.get("rule_fidelity")
    lines += [
        "",
        (
            f"Rule fidelity (human check of {fidelity['labelled']} rule outcomes): "
            f"**{fidelity['agreement']:.0%}** agreement."
            if fidelity
            else "Rule fidelity: not yet labelled (`ukco eval fidelity-sheet`)."
        ),
    ]
    lines += [
        "",
        "_Stratified sample: AUC (rank-based) is meaningful, precision is not. "
        "Filing-deadline flags partly cause compulsory strike-off, hence the row without them._",
    ]
    return "\n".join(lines) + "\n"


def write_results(out_dir: Path, results: list[CompanyResult], summary: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    # Per-company scores stay out of git: real companies, and a score is not a verdict.
    (out_dir / "companies.local.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2)
    )


FIDELITY_COLUMNS = [
    "company_number",
    "companies_house_url",
    "rule",
    "system_status",
    "system_says",
    "your_verdict",
    "your_note",
]


async def fidelity_sheet(
    snapshot: Path, out: Path, *, companies: int = 12, as_of: dt.date = AS_OF
) -> int:
    """A labelling sheet: does each rule read the register the way a human analyst would?

    For each company and rule, the sheet shows what the system concluded as of the eval date;
    a reviewer checks the Companies House page and writes agree / disagree in `your_verdict`."""
    import csv

    rows = []
    for company in list(read_snapshot(snapshot))[:companies]:
        bundle, evidence = await replay_bundle(company)
        a = assess(bundle, evidence, as_of=as_of)
        for o in a.outcomes:
            if o.rule == "officer_network":  # not assessed in back-tests
                continue
            says = "; ".join(f.explanation for f in o.flags) or o.note or ""
            rows.append(
                {
                    "company_number": company.company_number,
                    "companies_house_url": f"https://find-and-update.company-information.service.gov.uk/company/{company.company_number}/filing-history",
                    "rule": o.rule,
                    "system_status": o.status,
                    "system_says": says,
                    "your_verdict": "",
                    "your_note": "",
                }
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIDELITY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def fidelity_score(sheet: Path) -> dict[str, Any] | None:
    """Agreement per rule from a filled-in sheet; None until someone has labelled rows."""
    import csv

    with sheet.open() as f:
        rows = [
            r
            for r in csv.DictReader(f)
            if r["your_verdict"].strip().lower() in ("agree", "disagree")
        ]
    if not rows:
        return None
    per_rule: dict[str, list[bool]] = {}
    for r in rows:
        per_rule.setdefault(r["rule"], []).append(r["your_verdict"].strip().lower() == "agree")
    return {
        "labelled": len(rows),
        "agreement": round(sum(sum(v) for v in per_rule.values()) / len(rows), 3),
        "per_rule": {
            rule: {"n": len(v), "agreement": round(sum(v) / len(v), 3)}
            for rule, v in sorted(per_rule.items())
        },
    }
