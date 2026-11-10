"""Assemble a RiskReport from a company bundle and its rule assessment (no LLM)."""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ukco.config import get_settings
from ukco.evidence import Citation, EvidenceIndex, resolve_pointer
from ukco.ingest.company import CompanyBundle
from ukco.report.schema import (
    CheckResult,
    CompanySnapshot,
    EvidenceItem,
    FinancialFigure,
    OfficerSummary,
    Provenance,
    PscSummary,
    ReportFlag,
    RiskReport,
)
from ukco.risk.deadlines import add_months
from ukco.risk.models import Assessment
from ukco.risk.policy import SEVERITY_ORDER

SNAPSHOT_METRICS = (
    "net_assets",
    "net_current_assets",
    "current_assets",
    "cash",
    "turnover",
    "profit_loss",
    "employees",
)

_PATH_LABELS = (
    (re.compile(r"^/company/[A-Z0-9]{8}$"), "Company profile"),
    (re.compile(r"^/company/[A-Z0-9]{8}/officers$"), "Officer list"),
    (re.compile(r"^/company/[A-Z0-9]{8}/filing-history$"), "Filing history"),
    (re.compile(r"^/company/[A-Z0-9]{8}/persons-with-significant-control$"), "PSC register"),
    (
        re.compile(r"^/company/[A-Z0-9]{8}/persons-with-significant-control-statements$"),
        "PSC statements",
    ),
    (re.compile(r"^/company/[A-Z0-9]{8}/insolvency$"), "Insolvency register"),
    (re.compile(r"^/company/[A-Z0-9]{8}/charges$"), "Charges register"),
    (re.compile(r"^/officers/[^/]+/appointments$"), "Director's appointments"),
)
_CONCEPT = re.compile(r"name=([\w-]+:[\w-]+)")


def _when(value: object) -> str:
    return str(value)[:10] if value else "?"


def _item_label(path: str, item: dict[str, Any]) -> str | None:
    """What a single cited record says, in words an analyst can scan."""
    if path.endswith("/officers"):
        resigned = f", resigned {_when(item.get('resigned_on'))}" if item.get("resigned_on") else ""
        return (
            f"Officer: {item.get('name')} ({item.get('officer_role')}, "
            f"appointed {_when(item.get('appointed_on'))}{resigned})"
        )
    if path.endswith("/filing-history"):
        description = str(item.get("description") or "").replace("-", " ")
        return f"Filing {item.get('type')} on {_when(item.get('date'))}: {description}"[:140]
    if path.endswith("/persons-with-significant-control"):
        ceased = f", ceased {_when(item.get('ceased_on'))}" if item.get("ceased_on") else ""
        who = item.get("name") or item.get("kind")
        return f"PSC: {who} (notified {_when(item.get('notified_on'))}{ceased})"
    if path.endswith("/persons-with-significant-control-statements"):
        return f"PSC statement: {str(item.get('statement', '')).replace('-', ' ')}"
    if path.endswith("/appointments"):
        to = item.get("appointed_to") or {}
        return f"Other appointment: {to.get('company_name')} ({to.get('company_status')})"
    return None


def describe(c: Citation, bodies: Mapping[int, Any] | None = None) -> str:
    """A human label for a citation, from the record it points at when we have it."""
    if c.kind == "document":
        m = _CONCEPT.search(c.pointer)
        return f"Filed accounts, {m.group(1) if m else c.pointer}"
    base = next((label for pattern, label in _PATH_LABELS if pattern.match(c.path)), c.path)
    body = bodies.get(c.snapshot_id) if bodies and c.snapshot_id is not None else None
    if body is not None and c.pointer.startswith("/items/"):
        try:
            item = resolve_pointer(body, c.pointer)
        except (KeyError, IndexError, TypeError):
            item = None
        if isinstance(item, dict) and (label := _item_label(c.path, item)):
            return label
    if c.pointer and not c.pointer.startswith("/items/"):
        return f"{base}: {c.pointer.strip('/').replace('_', ' ')}"
    return base


class EvidenceRegistry:
    """Numbers citations E1, E2, … in order of first use; identical citations share an id."""

    def __init__(self, bodies: Mapping[int, Any] | None = None) -> None:
        self.items: list[EvidenceItem] = []
        self._ids: dict[tuple[str, str, str], str] = {}
        self._bodies = bodies

    def add(self, c: Citation, label: str | None = None) -> str:
        key = (c.kind, str(c.snapshot_id or c.document_id), c.pointer)
        if key not in self._ids:
            eid = f"E{len(self.items) + 1}"
            self._ids[key] = eid
            self.items.append(
                EvidenceItem(id=eid, label=label or describe(c, self._bodies), citation=c)
            )
        return self._ids[key]


def _snapshot(
    bundle: CompanyBundle,
    evidence: EvidenceIndex,
    as_of: dt.date,
    live: bool,
    reg: EvidenceRegistry,
) -> CompanySnapshot:
    p = bundle.profile
    n = bundle.company_number
    profile_id = reg.add(evidence.require("profile", n), "Company profile")
    officers_cite = evidence.get("register", f"/company/{n}/officers")
    pscs_cite = evidence.get("register", f"/company/{n}/persons-with-significant-control")
    ids = [profile_id] + [reg.add(c) for c in (officers_cite, pscs_cite) if c]

    year_ago = add_months(as_of, -12)
    appointed = [o for o in bundle.officers if not o.appointed_on or o.appointed_on <= as_of]
    current = [o for o in appointed if not o.resigned_on or o.resigned_on > as_of]
    resigned = [o for o in appointed if o.resigned_on and year_ago < o.resigned_on <= as_of]

    def officer(o) -> OfficerSummary:
        return OfficerSummary(
            name=o.name,
            role=o.officer_role,
            appointed_on=o.appointed_on,
            resigned_on=o.resigned_on if o.resigned_on and o.resigned_on <= as_of else None,
        )

    financials: list[FinancialFigure] = []
    series: dict[tuple[str, dt.date], FinancialFigure] = {}
    for doc in sorted(
        (d for d in bundle.accounts if d.filed_on <= as_of), key=lambda d: d.filed_on
    ):
        for f in doc.facts:
            if f.metric in SNAPSHOT_METRICS:
                cite = Citation.for_document(n, doc.transaction_id, doc.document_id, f.locator)
                series[(f.metric, f.period_end)] = FinancialFigure(
                    metric=f.metric,
                    period_end=f.period_end,
                    value=str(f.value),
                    evidence_id=reg.add(
                        cite, f"Filed accounts, {f.metric.replace('_', ' ')} at {f.period_end}"
                    ),
                )
    ends = sorted({end for (metric, end) in series if metric == "net_assets"})[-2:]
    financials = [fig for (metric, end), fig in sorted(series.items()) if end in ends]

    created = p.date_of_creation
    return CompanySnapshot(
        company_number=n,
        name=p.company_name,
        status=p.company_status,
        type=p.type,
        incorporated=created,
        age_years=round((as_of - created).days / 365.25, 1) if created else None,
        sic_codes=list(p.sic_codes),
        registered_office=p.registered_office_address.one_line()
        if p.registered_office_address
        else None,
        previous_names=[pn.name for pn in p.previous_company_names],
        current_officers=[officer(o) for o in current],
        recent_resignations=[officer(o) for o in resigned],
        pscs=[
            PscSummary(
                name=x.name,
                kind=x.kind,
                natures_of_control=list(x.natures_of_control),
                notified_on=x.notified_on,
                ceased_on=x.ceased_on if x.ceased_on and x.ceased_on <= as_of else None,
            )
            for x in bundle.pscs
            if not x.notified_on or x.notified_on <= as_of
        ],
        accounts_next_due=p.accounts.next_due if live and p.accounts else None,
        confirmation_statement_next_due=(
            p.confirmation_statement.next_due if live and p.confirmation_statement else None
        ),
        financials=financials,
        evidence_ids=ids,
    )


def build_report(
    bundle: CompanyBundle,
    evidence: EvidenceIndex,
    assessment: Assessment,
    *,
    ingestion_run_id: int | None = None,
    snapshot_ids: Sequence[int] = (),
) -> RiskReport:
    # Snapshot ids line up with the bundle's responses (persist_bundle stores them in order).
    reg = EvidenceRegistry(
        dict(zip(snapshot_ids, (r.body for r in bundle.responses), strict=False))
    )
    snapshot = _snapshot(bundle, evidence, assessment.as_of, assessment.live, reg)

    ordered = sorted(
        ((o.rule, f) for o in assessment.outcomes for f in o.flags),
        key=lambda rf: (-SEVERITY_ORDER[rf[1].severity], -rf[1].points),
    )
    flags: list[ReportFlag] = []
    flag_ids_by_rule: dict[str, list[str]] = {}
    for i, (rule, f) in enumerate(ordered, start=1):
        fid = f"F{i}"
        flag_ids_by_rule.setdefault(rule, []).append(fid)
        flags.append(
            ReportFlag(
                id=fid,
                flag_type=f.flag_type,
                severity=f.severity,
                points=f.points,
                title=f.title,
                explanation=f.explanation,
                evidence_ids=[reg.add(c) for c in f.evidence],
                details=f.details,
            )
        )

    checks = [
        CheckResult(
            rule=o.rule,
            status=o.status,
            note=o.note,
            flag_ids=flag_ids_by_rule.get(o.rule, []),
            evidence_ids=[reg.add(c) for c in o.evidence],
        )
        for o in assessment.outcomes
    ]
    return RiskReport(
        generated_at=dt.datetime.now(dt.UTC),
        as_of=assessment.as_of,
        live=assessment.live,
        company=snapshot,
        risk_score=assessment.risk_score,
        flags=flags,
        checks=checks,
        evidence=reg.items,
        provenance=Provenance(
            rules_version=get_settings().git_sha,
            policy_version=assessment.policy_version,
            policy_sha256=assessment.policy_sha256,
            ingestion_run_id=ingestion_run_id,
            snapshot_ids=list(snapshot_ids),
        ),
    )
