"""KYB tools exposed over MCP. Plain async methods over the same pipeline the API uses,
so an assistant gets exactly the reports (and review trail) an analyst would."""

import datetime as dt
from typing import Any

from sqlalchemy import Engine

from ukco.companies_house import CompaniesHouseClient, NotFoundError, normalise_company_number
from ukco.ingest.company import fetch_bundle, persist_bundle
from ukco.report import store
from ukco.report.evidence_values import evidence_value
from ukco.report.narrative import NarrativeWriter
from ukco.report.pipeline import generate_report
from ukco.report.schema import RiskReport
from ukco.report.validate import DbEvidenceStore

MAX_VALUE_CHARS = 1500  # evidence values are JSON excerpts; keep tool results compact
MAX_LINKED_COMPANIES = 15


class KybToolError(ValueError):
    """A tool failure the assistant should see as a clear message."""


def _number(value: str) -> str:
    try:
        return normalise_company_number(value)
    except ValueError as exc:
        raise KybToolError(str(exc)) from exc


class KybTools:
    def __init__(
        self,
        engine: Engine,
        client: CompaniesHouseClient,
        writer: NarrativeWriter | None,
        *,
        caller: str = "mcp",
    ) -> None:
        self.engine, self.client, self.writer, self.caller = engine, client, writer, caller

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            raise KybToolError("query is empty")
        result = await self.client.search_companies(query, limit=max(1, min(limit, 20)))
        return [
            {
                "company_number": c.company_number,
                "name": c.title,
                "status": c.company_status,
                "incorporated": str(c.date_of_creation) if c.date_of_creation else None,
                "address": c.address_snippet,
            }
            for c in result.items
        ]

    async def get_risk_report(
        self, company_number: str, as_of: str | None = None, check_network: bool = True
    ) -> dict[str, Any]:
        number = _number(company_number)
        as_of_date = _date(as_of)
        report_id = store.create_job(
            self.engine,
            number,
            as_of=as_of_date,
            requested_by=self.caller,
            params={
                "company_number": number,
                "as_of": as_of,
                "network": check_network,
                "source": "mcp",
            },
        )
        store.mark_running(self.engine, report_id)
        try:
            report = await generate_report(
                self.client,
                self.engine,
                number,
                as_of=as_of_date,
                network=check_network,
                writer=self.writer,
                requested_by=self.caller,
            )
        except NotFoundError as exc:
            store.mark_failed(self.engine, report_id, str(exc))
            raise KybToolError(f"No company {number} on Companies House") from exc
        except ValueError as exc:
            store.mark_failed(self.engine, report_id, str(exc))
            raise KybToolError(str(exc)) from exc
        store.save_success(self.engine, report_id, report)
        return _compact(report_id, report)

    async def get_flag_evidence(self, report_id: int, flag_id: str) -> dict[str, Any]:
        row = store.get_report(self.engine, report_id)
        if row is None or not row["payload"]:
            raise KybToolError(f"report {report_id} not found or not finished")
        report = RiskReport.model_validate(row["payload"])
        flag = next((f for f in report.flags if f.id == flag_id), None)
        if flag is None:
            raise KybToolError(
                f"report {report_id} has no flag {flag_id}; flags: {[f.id for f in report.flags]}"
            )
        evidence = report.evidence_by_id()
        store_ = DbEvidenceStore(self.engine)
        items = [
            evidence_value(self.engine, evidence[i], store_)
            for i in flag.evidence_ids
            if i in evidence
        ]
        return {
            "report_id": report_id,
            "flag": {
                "id": flag.id,
                "title": flag.title,
                "severity": flag.severity,
                "explanation": flag.explanation,
                "details": flag.details,
            },
            "evidence": items,
            "decisions": {
                k: {"decision": v.decision, "reviewer": v.reviewer, "note": v.note}
                for k, v in store.current_decisions(
                    store.list_reviews(self.engine, report_id)
                ).items()
                if k == flag.id
            },
        }

    async def officer_network(self, company_number: str) -> dict[str, Any]:
        number = _number(company_number)
        try:
            bundle = await fetch_bundle(self.client, number, network=True, financials=False)
        except NotFoundError as exc:
            raise KybToolError(f"No company {number} on Companies House") from exc
        with self.engine.begin() as conn:
            persist_bundle(conn, bundle)
        directors = []
        for officer_id, net in bundle.networks.items():
            others = [a for a in net.appointments if a.appointed_to.company_number != number]
            statuses: dict[str, int] = {}
            for a in others:
                key = a.appointed_to.company_status or "unknown"
                statuses[key] = statuses.get(key, 0) + 1
            directors.append(
                {
                    "officer_id": officer_id,
                    "name": net.name,
                    "total_appointments": net.total_results,
                    "truncated": net.truncated,
                    "other_companies_by_status": statuses,
                    "other_companies": [
                        {
                            "company_number": a.appointed_to.company_number,
                            "name": a.appointed_to.company_name,
                            "status": a.appointed_to.company_status,
                            "role": a.officer_role,
                            "appointed_on": str(a.appointed_on) if a.appointed_on else None,
                            "resigned_on": str(a.resigned_on) if a.resigned_on else None,
                        }
                        for a in others[:MAX_LINKED_COMPANIES]
                    ],
                }
            )
        return {
            "company_number": number,
            "company_name": bundle.profile.company_name,
            "directors_checked": len(directors),
            "not_checked": {
                "over_board_size_cap": len(bundle.network_skipped),
                "lookup_failed": len(bundle.network_errors),
            },
            "directors": directors,
            "note": "Secretaries and corporate officers are not network-checked.",
        }


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KybToolError(f"as_of must be YYYY-MM-DD, got {value!r}") from exc


def _compact(report_id: int, r: RiskReport) -> dict[str, Any]:
    evidence = r.evidence_by_id()
    return {
        "report_id": report_id,
        "company": {
            "number": r.company.company_number,
            "name": r.company.name,
            "status": r.company.status,
            "incorporated": str(r.company.incorporated),
            "sic_codes": r.company.sic_codes,
        },
        "as_of": str(r.as_of),
        "mode": "live" if r.live else "back-test",
        "risk_score": r.risk_score,
        "flags": [
            {
                "id": f.id,
                "severity": f.severity,
                "title": f.title,
                "explanation": f.explanation,
                "evidence": [
                    {"id": i, "label": evidence[i].label, "url": evidence[i].citation.url}
                    for i in f.evidence_ids
                    if i in evidence
                ],
            }
            for f in r.flags
        ],
        "checks": [{"rule": c.rule, "status": c.status, "note": c.note} for c in r.checks],
        "summary": r.narrative.summary if r.narrative else None,
        "disclaimer": r.disclaimer,
        "review": (
            f"Stored as report {report_id}; an analyst can confirm or dismiss each flag "
            "in the review UI."
        ),
    }
