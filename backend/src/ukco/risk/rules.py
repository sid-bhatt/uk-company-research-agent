"""Deterministic KYB rules over a company bundle, evaluated *as of* a date.

Every rule:
- only uses records dated on or before `as_of` (so back-tests don't see the future)
- returns flagged / clear / not_assessed, never silently nothing
- cites the exact API records behind every flag (a Flag without evidence is invalid)

Live-only signals (profile booleans, current statuses) are used only when `as_of` is
the fetch date; in back-tests those rules report `not_assessed` rather than leak.
"""

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from ukco.companies_house.models import FilingHistoryItem, Officer
from ukco.evidence import Citation, EvidenceIndex
from ukco.financials.extract import FinancialFact
from ukco.ingest.company import AccountsDocument, CompanyBundle
from ukco.risk.deadlines import accounts_due, add_months, confirmation_statement_due
from ukco.risk.models import Flag, RuleOutcome
from ukco.risk.policy import Policy, Severity, band_for_changes, band_for_days, worst

CLOSURE_FILING_PREFIXES = ("LIQ", "AM", "GAZ2", "DS01", "COCOMP", "4.", "2.")
MAX_CITATIONS_PER_FLAG = 10


@dataclass(frozen=True)
class RuleContext:
    bundle: CompanyBundle
    evidence: EvidenceIndex
    as_of: dt.date
    policy: Policy

    @property
    def n(self) -> str:
        return self.bundle.company_number

    @property
    def live(self) -> bool:
        return self.as_of >= self.bundle.fetched_at.date()

    @property
    def incorporated(self) -> dt.date:
        created = self.bundle.profile.date_of_creation
        assert created, "company has no incorporation date"
        return created

    def filings(self, *types: str) -> list[FilingHistoryItem]:
        """Filings on or before as_of, newest first, optionally of given types."""
        return [
            f
            for f in self.bundle.filings
            if f.date and f.date <= self.as_of and (not types or f.type in types)
        ]

    def profile(self, pointer: str = "") -> Citation:
        return self.evidence.require("profile", self.n).at(pointer)

    def filing(self, f: FilingHistoryItem) -> Citation:
        return self.evidence.require("filing", self.n, f.transaction_id)

    def register(self, suffix: str) -> Citation:
        return self.evidence.require("register", f"/company/{self.n}{suffix}")

    def flag(
        self,
        flag_type: str,
        severity: Severity,
        title: str,
        explanation: str,
        evidence: Iterable[Citation],
        **details: object,
    ) -> Flag:
        return Flag(
            flag_type=flag_type,
            severity=severity,
            points=self.policy.points[severity],
            title=title,
            explanation=explanation,
            evidence=_unique(evidence)[:MAX_CITATIONS_PER_FLAG],
            details=details,
        )


def _unique(citations: Iterable[Citation]) -> list[Citation]:
    return list({(c.snapshot_id, c.pointer): c for c in citations}.values())


def _made_up_date(f: FilingHistoryItem) -> dt.date | None:
    value = f.description_values.get("made_up_date")
    return dt.date.fromisoformat(str(value)) if value else None


def _in_window(
    d: dt.date | None, ctx: RuleContext, months: int, *, after: dt.date | None = None
) -> dt.date | None:
    """`d` if it falls in the `months` up to as_of (and strictly after `after`), else None."""
    if d is None or not add_months(ctx.as_of, -months) < d <= ctx.as_of:
        return None
    return d if after is None or d > after else None


def _closed_or_insolvent(ctx: RuleContext) -> str | None:
    """Filing obligations stop in liquidation/administration/dissolution."""
    if ctx.live and ctx.bundle.profile.company_status not in (None, "active"):
        return f"company status is '{ctx.bundle.profile.company_status}'"
    for f in ctx.filings():
        if f.type and f.type.startswith(CLOSURE_FILING_PREFIXES):
            return f"closure/insolvency filing {f.type} on {f.date}"
    return None


# --- Rules -----------------------------------------------------------------------------


def overdue_accounts(ctx: RuleContext) -> RuleOutcome:
    rule = "overdue_accounts"
    if reason := _closed_or_insolvent(ctx):
        return RuleOutcome(rule=rule, status="not_assessed", note=f"Not required: {reason}.")

    filed = [(m, f) for f in ctx.filings("AA") if (m := _made_up_date(f))]
    last_made_up, last_filing = max(filed, key=lambda x: x[0]) if filed else (None, None)
    _, computed_due = accounts_due(ctx.bundle.profile.type, ctx.incorporated, last_made_up)

    accounts = ctx.bundle.profile.accounts
    if ctx.live and accounts and accounts.next_due:
        due, overdue, method = accounts.next_due, bool(accounts.overdue), "register"
        evidence = [ctx.profile("/accounts")]
    else:
        due, overdue, method = computed_due, ctx.as_of > computed_due, "computed"
        evidence = [ctx.register("/filing-history"), ctx.profile("/date_of_creation")]
    if last_filing:
        evidence.append(ctx.filing(last_filing))

    days = (ctx.as_of - due).days
    details = {
        "due": due.isoformat(),
        "last_made_up_to": last_made_up and last_made_up.isoformat(),
        "method": method,
        "computed_due": computed_due.isoformat(),
    }
    severity = band_for_days(ctx.policy.rules.overdue_accounts.severity_by_days_overdue, days)
    if not overdue or severity is None:
        return RuleOutcome(rule=rule, status="clear", note=f"Next accounts due {due}.")
    since = (
        f"since last accounts made up to {last_made_up}"
        if last_made_up
        else "no accounts ever filed"
    )
    return RuleOutcome(
        rule=rule,
        status="flagged",
        flags=[
            ctx.flag(
                rule,
                severity,
                "Accounts overdue",
                f"Annual accounts were due on {due} and are {days} days late ({since}).",
                evidence,
                days_overdue=days,
                **details,
            )
        ],
    )


def overdue_confirmation_statement(ctx: RuleContext) -> RuleOutcome:
    rule = "overdue_confirmation_statement"
    if reason := _closed_or_insolvent(ctx):
        return RuleOutcome(rule=rule, status="not_assessed", note=f"Not required: {reason}.")

    filed = [(m, f) for f in ctx.filings("CS01", "AR01") if (m := _made_up_date(f))]
    last_made_up, last_filing = max(filed, key=lambda x: x[0]) if filed else (None, None)
    _, computed_due = confirmation_statement_due(ctx.incorporated, last_made_up)

    cs = ctx.bundle.profile.confirmation_statement
    if ctx.live and cs and cs.next_due:
        due, overdue, method = cs.next_due, bool(cs.overdue), "register"
        evidence = [ctx.profile("/confirmation_statement")]
    else:
        due, overdue, method = computed_due, ctx.as_of > computed_due, "computed"
        evidence = [ctx.register("/filing-history"), ctx.profile("/date_of_creation")]
    if last_filing:
        evidence.append(ctx.filing(last_filing))

    days = (ctx.as_of - due).days
    bands = ctx.policy.rules.overdue_confirmation_statement.severity_by_days_overdue
    severity = band_for_days(bands, days)
    if not overdue or severity is None:
        return RuleOutcome(
            rule=rule, status="clear", note=f"Next confirmation statement due {due}."
        )
    return RuleOutcome(
        rule=rule,
        status="flagged",
        flags=[
            ctx.flag(
                rule,
                severity,
                "Confirmation statement overdue",
                f"The confirmation statement was due on {due} and is {days} days late.",
                evidence,
                days_overdue=days,
                due=due.isoformat(),
                method=method,
                computed_due=computed_due.isoformat(),
            )
        ],
    )


def _is_director(o: Officer) -> bool:
    return "secretary" not in o.officer_role


def director_churn(ctx: RuleContext) -> RuleOutcome:
    rule = "director_churn"
    cfg = ctx.policy.rules.director_churn
    initial_board_until = ctx.incorporated + dt.timedelta(days=cfg.ignore_days_after_incorporation)

    events: list[tuple[dt.date, str, Officer]] = []
    for o in ctx.bundle.officers:
        if not _is_director(o):
            continue
        m = cfg.window_months
        if appointed := _in_window(o.appointed_on, ctx, m, after=initial_board_until):
            events.append((appointed, "appointed", o))
        if resigned := _in_window(o.resigned_on, ctx, m):
            events.append((resigned, "resigned", o))

    # A same-day resignation + appointment of the same person is a role change, not churn.
    resigned_on = {(o.officer_id, d) for d, kind, o in events if kind == "resigned"}
    appointed_on = {(o.officer_id, d) for d, kind, o in events if kind == "appointed"}
    role_changes = resigned_on & appointed_on
    events = sorted(
        (e for e in events if (e[2].officer_id, e[0]) not in role_changes), key=lambda e: e[0]
    )

    changes = len(events)
    severity = band_for_changes(cfg.severity_by_changes, changes)
    if severity is None:
        return RuleOutcome(
            rule=rule,
            status="clear",
            note=f"{changes} director change(s) in the {cfg.window_months} months to {ctx.as_of}.",
        )
    evidence = [
        ctx.evidence.get(
            "appointment",
            ctx.n,
            o.officer_id,
            o.officer_role,
            o.appointed_on.isoformat() if o.appointed_on else None,
        )
        or ctx.register("/officers")
        for _, _, o in events
    ]
    return RuleOutcome(
        rule=rule,
        status="flagged",
        flags=[
            ctx.flag(
                rule,
                severity,
                "High director turnover",
                f"{changes} director appointments/resignations "
                f"in the {cfg.window_months} months to {ctx.as_of}.",
                evidence,
                changes=changes,
                events=[
                    {
                        "date": d.isoformat(),
                        "event": kind,
                        "officer": o.name,
                        "role": o.officer_role,
                    }
                    for d, kind, o in events
                ],
            )
        ],
    )


def psc_changes(ctx: RuleContext) -> RuleOutcome:
    rule = "psc_changes"
    cfg = ctx.policy.rules.psc_changes
    initial_until = ctx.incorporated + dt.timedelta(days=cfg.ignore_days_after_incorporation)
    events: list[tuple[dt.date, str, str, Citation]] = []

    for p in ctx.bundle.pscs:
        if not p.psc_id:
            continue
        cite = ctx.evidence.get("psc", ctx.n, p.psc_id) or ctx.register(
            "/persons-with-significant-control"
        )
        who = p.name or p.kind
        if notified := _in_window(p.notified_on, ctx, cfg.window_months, after=initial_until):
            events.append((notified, "new controller", who, cite))
        if ceased := _in_window(p.ceased_on, ctx, cfg.window_months):
            events.append((ceased, "controller ceased", who, cite))
    for s in ctx.bundle.psc_statements:
        notified = s.notified_on.isoformat() if s.notified_on else None
        cite = ctx.evidence.get("psc_statement", ctx.n, s.statement, notified) or ctx.register(
            "/persons-with-significant-control-statements"
        )
        if filed := _in_window(s.notified_on, ctx, cfg.window_months, after=initial_until):
            events.append((filed, "statement filed", s.statement, cite))

    events.sort(key=lambda e: e[0])
    changes = len(events)
    severity = band_for_changes(cfg.severity_by_changes, changes)
    if severity is None:
        note = f"No ownership changes in the {cfg.window_months} months to {ctx.as_of}."
        if not ctx.bundle.pscs and ctx.bundle.profile.type == "plc":
            note += " Listed companies are exempt from the PSC register."
        return RuleOutcome(rule=rule, status="clear", note=note)
    return RuleOutcome(
        rule=rule,
        status="flagged",
        flags=[
            ctx.flag(
                rule,
                severity,
                "Recent ownership changes",
                f"{changes} change(s) to people with significant control "
                f"in the {cfg.window_months} months to {ctx.as_of}.",
                [c for *_, c in events],
                changes=changes,
                events=[
                    {"date": d.isoformat(), "event": kind, "who": who} for d, kind, who, _ in events
                ],
            )
        ],
    )


def office_changes(ctx: RuleContext) -> RuleOutcome:
    rule = "office_changes"
    cfg = ctx.policy.rules.office_changes
    flags: list[Flag] = []

    moves: list[tuple[dt.date, FilingHistoryItem]] = []
    for f in ctx.filings("AD01"):
        raw = f.description_values.get("change_date")
        if changed := _in_window(
            dt.date.fromisoformat(str(raw)) if raw else f.date, ctx, cfg.window_months
        ):
            moves.append((changed, f))
    severity = band_for_changes(cfg.severity_by_changes, len(moves))
    if severity:
        flags.append(
            ctx.flag(
                rule,
                severity,
                "Frequent registered-office changes",
                f"The registered office changed {len(moves)} times "
                f"in the {cfg.window_months} months to {ctx.as_of}.",
                [ctx.filing(f) for _, f in moves],
                changes=len(moves),
                dates=sorted(d.isoformat() for d, _ in moves),
            )
        )

    p = ctx.bundle.profile
    if ctx.live:
        for field, label in (
            ("registered_office_is_in_dispute", "disputed"),
            ("undeliverable_registered_office_address", "marked undeliverable"),
        ):
            if getattr(p, field):
                flags.append(
                    ctx.flag(
                        "registered_office_problem",
                        cfg.disputed_or_undeliverable_severity,
                        "Registered office problem",
                        f"Companies House records the registered office address as {label}.",
                        [ctx.profile(f"/{field}")],
                        marker=field,
                    )
                )

    if flags:
        return RuleOutcome(rule=rule, status="flagged", flags=flags)
    return RuleOutcome(
        rule=rule,
        status="clear",
        note=f"{len(moves)} office change(s) in the {cfg.window_months} months to {ctx.as_of}.",
    )


def new_company_high_risk_sic(ctx: RuleContext) -> RuleOutcome:
    rule = "new_company_high_risk_sic"
    cfg = ctx.policy.rules.new_company_high_risk_sic
    young = ctx.as_of < add_months(ctx.incorporated, cfg.max_age_months)
    risky = {
        c: ctx.policy.high_risk_sic_codes[c]
        for c in ctx.bundle.profile.sic_codes
        if c in ctx.policy.high_risk_sic_codes
    }
    caveat = "" if ctx.live else " SIC codes are the company's current ones."
    note = caveat.strip() or None
    if not (young and risky):
        age = "under" if young else "over"
        sector = "a higher-risk" if risky else "no higher-risk"
        return RuleOutcome(
            rule=rule,
            status="clear",
            note=f"{age.capitalize()} {cfg.max_age_months} months old; {sector} SIC code.{caveat}",
        )
    age_days = (ctx.as_of - ctx.incorporated).days
    return RuleOutcome(
        rule=rule,
        status="flagged",
        note=note,
        flags=[
            ctx.flag(
                rule,
                cfg.severity,
                "New company in a higher-risk sector",
                f"Incorporated {age_days} days before {ctx.as_of} with higher-risk activity: "
                + "; ".join(f"{c} ({d})" for c, d in risky.items())
                + ".",
                [ctx.profile("/date_of_creation"), ctx.profile("/sic_codes")],
                age_days=age_days,
                sic_codes=list(risky),
            )
        ],
    )


def officer_network(ctx: RuleContext) -> RuleOutcome:
    rule = "officer_network"
    cfg = ctx.policy.rules.officer_network
    if not ctx.live:
        # The appointments API reports linked companies' *current* status; using it for a
        # past as_of would leak what happened afterwards.
        return RuleOutcome(
            rule=rule,
            status="not_assessed",
            note="Linked companies' statuses are only known as of today.",
        )
    b = ctx.bundle
    if not b.networks:
        reason = "no eligible directors" if not b.network_errors else "network lookups failed"
        return RuleOutcome(rule=rule, status="not_assessed", note=f"No network data ({reason}).")

    hits: list[dict[str, object]] = []
    evidence: list[Citation] = []
    severities: list[Severity] = []
    for officer_id, net in b.networks.items():
        if net.is_corporate_officer:
            continue
        links = {
            a.appointed_to.company_number: a
            for a in net.appointments
            if a.appointed_to.company_number != ctx.n
        }
        adverse = {
            k: a for k, a in links.items() if a.appointed_to.company_status in cfg.adverse_statuses
        }
        insolvent = {
            k: a
            for k, a in adverse.items()
            if a.appointed_to.company_status in cfg.insolvency_statuses
        }
        share = len(adverse) / len(links) if links else 0.0
        by_volume = len(adverse) >= cfg.min_adverse_links and share >= cfg.min_adverse_share
        by_insolvency = len(insolvent) >= cfg.min_insolvency_links
        if not (by_volume or by_insolvency):
            continue
        severity = cfg.severity_if_insolvency_links if by_insolvency else cfg.severity
        severities.append(severity)
        hits.append(
            {
                "officer_id": officer_id,
                "officer": net.name,
                "linked_companies": len(links),
                "adverse": len(adverse),
                "insolvent": len(insolvent),
                "share": round(share, 2),
                "truncated": net.truncated,
            }
        )
        evidence.append(ctx.evidence.require("network_summary", officer_id))
        for k, a in list(insolvent.items()) + list(adverse.items()):
            c = ctx.evidence.get(
                "network",
                officer_id,
                k,
                a.officer_role,
                a.appointed_on.isoformat() if a.appointed_on else None,
            )
            if c:
                evidence.append(c)

    caveats = []
    if b.network_skipped:
        caveats.append(f"{len(b.network_skipped)} director(s) not checked (board size cap)")
    if b.network_errors:
        caveats.append(f"{len(b.network_errors)} lookup(s) failed")
    if any(n.truncated for n in b.networks.values()):
        caveats.append("some directors' appointment lists were truncated")
    note = "; ".join(caveats) or None

    if not hits:
        return RuleOutcome(
            rule=rule,
            status="clear",
            note=note or f"{len(b.networks)} director network(s) checked.",
        )
    names = ", ".join(f"{h['officer']} ({h['adverse']} of {h['linked_companies']})" for h in hits)
    return RuleOutcome(
        rule=rule,
        status="flagged",
        note=note,
        flags=[
            ctx.flag(
                rule,
                worst(severities) or cfg.severity,
                "Directors linked to failed companies",
                f"Director(s) with many dissolved or insolvent other companies: {names}.",
                evidence,
                officers=hits,
            )
        ],
    )


def _money(value: Decimal) -> str:
    return f"-£{-value:,.0f}" if value < 0 else f"£{value:,.0f}"


def financial_deterioration(ctx: RuleContext) -> RuleOutcome:
    rule = "financial_deterioration"
    cfg = ctx.policy.rules.financial_deterioration
    filed = [d for d in ctx.bundle.accounts if d.filed_on <= ctx.as_of]
    usable = [d for d in filed if d.facts]
    if not usable:
        if not filed:
            reason = "no accounts filed" + ("" if ctx.live else f" by {ctx.as_of}")
        elif any(d.format == "pdf" for d in filed):
            reason = "accounts are PDF-only (large or paper filers; OCR fallback is post-v1)"
        else:
            reason = "no figures could be extracted from the filed accounts"
        return RuleOutcome(
            rule=rule, status="not_assessed", note=reason[0].upper() + reason[1:] + "."
        )

    # metric → period end → (fact, document); a later filing wins for the same period
    # (restated comparatives).
    series: dict[str, dict[dt.date, tuple[FinancialFact, AccountsDocument]]] = {}
    for doc in sorted(usable, key=lambda d: d.filed_on):
        for fact in doc.facts:
            series.setdefault(fact.metric, {})[fact.period_end] = (fact, doc)

    def cite(entry: tuple[FinancialFact, AccountsDocument]) -> Citation:
        fact, doc = entry
        return Citation.for_document(ctx.n, doc.transaction_id, doc.document_id, fact.locator)

    net_assets = series.get("net_assets", {})
    periods = sorted(net_assets)
    if not periods:
        return RuleOutcome(
            rule=rule, status="not_assessed", note="Filed accounts carry no net assets figure."
        )
    latest = periods[-1]
    prior = periods[-2] if len(periods) > 1 else None
    figures = {m: {str(p): str(e[0].value) for p, e in s.items()} for m, s in series.items()}
    flags: list[Flag] = []

    drops = []
    for metric in cfg.drop_metrics:
        s = series.get(metric, {})
        if prior and latest in s and prior in s:
            before, after = s[prior][0].value, s[latest][0].value
            if before >= Decimal(str(cfg.min_prior_value)) and (before - after) / before >= Decimal(
                str(cfg.min_drop_share)
            ):
                drops.append((metric, before, after, s[prior], s[latest]))
    if drops:
        described = "; ".join(
            f"{m.replace('_', ' ')} {_money(b)} → {_money(a)} ({(b - a) / b:.0%} down)"
            for m, b, a, *_ in drops
        )
        flags.append(
            ctx.flag(
                rule,
                cfg.drop_severity,
                "Financial deterioration",
                f"Between the year-ends {prior} and {latest}: {described}.",
                [
                    c
                    for *_, before_fact, after_fact in drops
                    for c in (cite(before_fact), cite(after_fact))
                ],
                period_end=str(latest),
                prior_period_end=str(prior),
                figures=figures,
            )
        )

    now = net_assets[latest][0].value
    if now < 0:
        before = net_assets[prior][0].value if prior else None
        turned = before is not None and before >= 0
        evidence = [cite(net_assets[latest])] + ([cite(net_assets[prior])] if prior else [])
        flags.append(
            ctx.flag(
                "net_liabilities",
                cfg.turned_negative_severity if turned else cfg.net_liabilities_severity,
                "Net assets turned negative" if turned else "Net liabilities",
                f"Net assets were {_money(now)} at {latest}"
                + (
                    f", down from {_money(before)} at {prior}."
                    if turned and before is not None
                    else "."
                ),
                evidence,
                net_assets=str(now),
                period_end=str(latest),
            )
        )

    if flags:
        return RuleOutcome(rule=rule, status="flagged", flags=flags)
    note = f"Net assets {_money(now)} at {latest}"
    if prior:
        note += f" ({_money(net_assets[prior][0].value)} at {prior})"
    if usable[0].is_dormant:
        note += "; dormant company accounts"
    return RuleOutcome(rule=rule, status="clear", note=note + ".")


RULES: list[Callable[[RuleContext], RuleOutcome]] = [
    overdue_accounts,
    overdue_confirmation_statement,
    director_churn,
    psc_changes,
    office_changes,
    new_company_high_risk_sic,
    officer_network,
    financial_deterioration,
]
