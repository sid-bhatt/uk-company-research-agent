"""Render a RiskReport as Markdown: the human-readable form of the JSON contract."""

from decimal import Decimal

from ukco.report.schema import RiskReport

SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
STATUS_ICON = {"flagged": "✗", "clear": "✓", "not_assessed": "·", "noted": "!"}


def _money(value: str) -> str:
    d = Decimal(value)
    return f"-£{-d:,.0f}" if d < 0 else f"£{d:,.0f}"


def render_markdown(r: RiskReport) -> str:
    ev = r.evidence_by_id()

    def cite(ids: list[str]) -> str:
        return " ".join(f"[{i}]({ev[i].citation.url})" for i in ids if i in ev)

    c = r.company
    mode = "live" if r.live else "back-test"
    out = [
        f"# KYB report: {c.name} ({c.company_number})",
        "",
        f"**Risk score {r.risk_score}/100** · as of {r.as_of} ({mode}) · "
        f"{len(r.flags)} flag(s) · generated {r.generated_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"> {r.disclaimer}",
        "",
    ]

    if r.narrative:
        out += [
            "## Summary",
            "",
            f"{r.narrative.summary} {cite(r.narrative.summary_evidence_ids)}",
            "",
        ]

    out += ["## Red flags", ""]
    if not r.flags:
        out += ["No red flags raised.", ""]
    llm = {e.flag_id: e for e in r.narrative.flag_explanations} if r.narrative else {}
    for f in r.flags:
        out += [
            f"### {SEVERITY_ICON[f.severity]} {f.id} · {f.title} ({f.severity}, +{f.points})",
            "",
            f"{f.explanation} {cite(f.evidence_ids)}",
        ]
        if f.id in llm:
            out += ["", f"*Why it matters:* {llm[f.id].text}"]
        out.append("")

    if r.narrative and r.narrative.next_steps:
        out += ["## Suggested next steps", "", *(f"- {s}" for s in r.narrative.next_steps), ""]

    out += [
        "## Company snapshot",
        "",
        f"| | |\n|---|---|\n| Status | {c.status} |\n| Type | {c.type} |",
        f"| Incorporated | {c.incorporated} ({c.age_years} years) |",
        f"| SIC codes | {', '.join(c.sic_codes) or '-'} |",
        f"| Registered office | {c.registered_office or '-'} |",
    ]
    if c.previous_names:
        out.append(f"| Previous names | {', '.join(c.previous_names)} |")
    if c.accounts_next_due:
        out.append(f"| Accounts next due | {c.accounts_next_due} |")
    if c.confirmation_statement_next_due:
        out.append(f"| Confirmation statement next due | {c.confirmation_statement_next_due} |")
    out += ["", f"Sources: {cite(c.evidence_ids)}", ""]

    if c.current_officers:
        out += ["**Current officers**", ""]
        out += [f"- {o.name}, {o.role} (appointed {o.appointed_on})" for o in c.current_officers]
        out.append("")
    if c.recent_resignations:
        out += ["**Resigned in the last 12 months**", ""]
        out += [f"- {o.name}, {o.role} (resigned {o.resigned_on})" for o in c.recent_resignations]
        out.append("")
    if c.pscs:
        out += ["**People with significant control**", ""]
        for p in c.pscs:
            ceased = f", ceased {p.ceased_on}" if p.ceased_on else ""
            out.append(f"- {p.name or p.kind} (notified {p.notified_on}{ceased})")
        out.append("")
    if c.financials:
        ends = sorted({f.period_end for f in c.financials})
        out += [
            "**Filed accounts**",
            "",
            "| Metric | " + " | ".join(str(e) for e in ends) + " |",
            "|---|" + "---:|" * len(ends),
        ]
        by_metric: dict[str, dict] = {}
        for f in c.financials:
            by_metric.setdefault(f.metric, {})[f.period_end] = f
        for metric, cells in by_metric.items():
            row = []
            for e in ends:
                fig = cells.get(e)
                value = (fig.value if metric == "employees" else _money(fig.value)) if fig else "-"
                row.append(f"{value} {cite([fig.evidence_id])}" if fig else value)
            out.append(f"| {metric.replace('_', ' ')} | " + " | ".join(row) + " |")
        out.append("")

    out += ["## All checks", ""]
    out += [
        f"- {STATUS_ICON[k.status]} **{k.rule}**: {k.note or ', '.join(k.flag_ids)}"
        for k in r.checks
    ]
    out += ["", "## Evidence", ""]
    out += [f"- **{e.id}** [{e.label}]({e.citation.url})" for e in r.evidence]

    p = r.provenance
    out += [
        "",
        "## Provenance",
        "",
        f"rules `{p.rules_version}` · policy v{p.policy_version} (`{p.policy_sha256[:12]}`) · "
        f"ingestion run {p.ingestion_run_id} · {len(p.snapshot_ids)} stored API responses",
    ]
    if p.llm:
        tokens = f"{p.llm.input_tokens}+{p.llm.output_tokens} tokens"
        out.append(
            f"narrative: {p.narrative_status} · {p.llm.model} ({p.llm.prompt_version}) · "
            f"{tokens} · ${p.llm.cost_usd:.4f} · {p.llm.latency_ms} ms"
        )
    else:
        out.append(
            f"narrative: {p.narrative_status}"
            + (f" ({p.narrative_note})" if p.narrative_note else "")
        )
    return "\n".join(out) + "\n"
