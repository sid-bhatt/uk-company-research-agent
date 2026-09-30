"""Frozen eval snapshots: record once from the live API, replay offline forever.

CI never calls Companies House: evals replay these recorded responses through the real
client, so the whole pipeline (parsing, pagination, rules) runs exactly as in production,
reproducibly, with no rate limit and no data drift between PRs.

People's names and addresses are pseudonymised before anything is written (one fresh
pseudonymiser per company, so people can't be linked across companies in the file).
Accounts are stored as extracted figures, not documents: filed accounts contain
directors' names in their approval statements.
"""

import datetime as dt
import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from ukco.companies_house import CompaniesHouseClient, RateLimiter, RetryPolicy
from ukco.evidence import EvidenceIndex, StoredResponse
from ukco.financials.extract import FinancialFact
from ukco.ingest.company import AccountsDocument, CompanyBundle, fetch_bundle
from ukco.privacy import Pseudonymiser


@dataclass
class FrozenCompany:
    company_number: str
    stratum: str
    outcome: str
    event_date: str | None
    label_evidence: str | None
    fetched_at: str  # ISO datetime; replayed as the bundle's fetch time
    responses: list[dict[str, Any]] = field(default_factory=list)  # {path, params, body}
    accounts: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_bundle(
        cls,
        bundle: CompanyBundle,
        *,
        stratum: str,
        outcome: str,
        event_date: dt.date | None,
        evidence: str | None,
    ) -> "FrozenCompany":
        pseudo = Pseudonymiser()
        return cls(
            company_number=bundle.company_number,
            stratum=stratum,
            outcome=outcome,
            event_date=event_date.isoformat() if event_date else None,
            label_evidence=evidence,
            fetched_at=bundle.fetched_at.isoformat(),
            responses=[
                {
                    "path": r.path,
                    "params": {k: str(v) for k, v in r.params.items()},
                    "body": pseudo(r.body),
                }
                for r in bundle.responses
                if not r.path.startswith("/document/")
            ],
            accounts=[
                {
                    "transaction_id": d.transaction_id,
                    "document_id": d.document_id,
                    "filed_on": d.filed_on.isoformat(),
                    "made_up_to": d.made_up_to.isoformat() if d.made_up_to else None,
                    "description": d.description,
                    "format": d.format,
                    "error": d.error,
                    "facts": [
                        {
                            "metric": f.metric,
                            "period_start": f.period_start and f.period_start.isoformat(),
                            "period_end": f.period_end.isoformat(),
                            "value": str(f.value),
                            "unit": f.unit,
                            "concept": f.concept,
                            "locator": f.locator,
                        }
                        for f in d.facts
                    ],
                }
                for d in bundle.accounts
            ],
        )


def write_snapshot(path: Path, companies: list[FrozenCompany]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for c in sorted(companies, key=lambda c: c.company_number):
            f.write(json.dumps(c.__dict__, sort_keys=True, separators=(",", ":")) + "\n")


def read_snapshot(path: Path) -> Iterator[FrozenCompany]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield FrozenCompany(**json.loads(line))


def _key(path: str, params: dict[str, Any]) -> str:
    return json.dumps([path, {k: str(v) for k, v in sorted(params.items())}])


class FrozenTransport(httpx.AsyncBaseTransport):
    """Serves recorded responses; `{}` bodies were 404s ("none on file") and replay as such."""

    def __init__(self, company: FrozenCompany) -> None:
        self.routes = {_key(r["path"], r["params"]): r["body"] for r in company.responses}
        self.misses: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = _key(request.url.path, dict(request.url.params))
        body = self.routes.get(key)
        if body is None:
            self.misses.append(key)
        if not body:
            return httpx.Response(404, json={"errors": [{"error": "not-found"}]})
        return httpx.Response(200, json=body)


async def replay_bundle(company: FrozenCompany) -> tuple[CompanyBundle, EvidenceIndex]:
    """Rebuild the company's bundle offline, through the real client and parsers."""

    async def no_sleep(_: float) -> None:
        return None

    fetched_at = dt.datetime.fromisoformat(company.fetched_at)
    client = CompaniesHouseClient(
        "frozen",
        transport=FrozenTransport(company),
        limiter=RateLimiter(sleep=no_sleep),
        retry=RetryPolicy(max_attempts=1),
        sleep=no_sleep,
    )
    async with client:
        bundle = await fetch_bundle(
            client, company.company_number, network=False, financials=False, today=fetched_at.date()
        )
    bundle.fetched_at = fetched_at
    bundle.accounts = [
        AccountsDocument(
            transaction_id=a["transaction_id"],
            document_id=a["document_id"],
            filed_on=dt.date.fromisoformat(a["filed_on"]),
            made_up_to=dt.date.fromisoformat(a["made_up_to"]) if a["made_up_to"] else None,
            description=a["description"],
            format=a["format"],
            error=a["error"],
            facts=[
                FinancialFact(
                    metric=f["metric"],
                    period_start=dt.date.fromisoformat(f["period_start"])
                    if f["period_start"]
                    else None,
                    period_end=dt.date.fromisoformat(f["period_end"]),
                    value=Decimal(f["value"]),
                    unit=f["unit"],
                    concept=f["concept"],
                    locator=f["locator"],
                    document_id=a["document_id"],
                )
                for f in a["facts"]
            ],
        )
        for a in company.accounts
    ]
    evidence = EvidenceIndex.build(
        StoredResponse(i + 1, r.path, r.params, r.body) for i, r in enumerate(bundle.responses)
    )
    return bundle, evidence
