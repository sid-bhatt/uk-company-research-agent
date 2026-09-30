"""Shared test doubles: a fake Companies House serving recorded fixtures."""

import datetime as dt
import json
from typing import Any

import httpx

from tests.conftest import FIXTURES, load_fixture
from ukco.companies_house import CompaniesHouseClient, RateLimiter, RetryPolicy
from ukco.companies_house.models import Officer as ApiOfficer

GREGGS = "00502851"
ACTIVE, LIQUIDATION = "13237895", "13236560"
TODAY = dt.date(2026, 9, 28)  # fixes the 3-year document and network windows


def kyb(name: str) -> Any:
    return json.loads((FIXTURES / "companies_house" / "kyb" / f"{name}.json").read_text())


class FakeCompaniesHouse:
    """Path → response table. Tests mutate entries between runs; unknown paths 404
    (for PSCs/charges/insolvency that means "none on file")."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {
            f"/company/{GREGGS}": load_fixture("company_greggs"),
            f"/company/{GREGGS}/officers": load_fixture("officers_greggs"),
            f"/company/{GREGGS}/filing-history": load_fixture("filing_history_greggs_accounts"),
        }
        for label, n in (("active", ACTIVE), ("liquidation", LIQUIDATION)):
            base = f"/company/{n}"
            for suffix, name in (
                ("", "profile"),
                ("/officers", "officers"),
                ("/persons-with-significant-control", "persons_with_significant_control"),
                (
                    "/persons-with-significant-control-statements",
                    "persons_with_significant_control_statements",
                ),
                ("/charges", "charges"),
                ("/insolvency", "insolvency"),
                ("/filing-history", "filing_history"),
            ):
                self.routes[base + suffix] = kyb(f"{label}_{name}")
        # The recorded network belongs to the active company's first officer.
        self.networked_officer = first_officer_id(self.routes[f"/company/{ACTIVE}/officers"])
        self.routes[f"/officers/{self.networked_officer}/appointments"] = kyb(
            "active_officer_appointments"
        )
        # document_id → iXBRL bytes, or None for a PDF-only document. Unknown ids 404.
        self.documents: dict[str, bytes | None] = {}

    def accounts_document_ids(self, number: str) -> list[str]:
        """Document ids of a company's accounts filings, newest first."""
        items = self.routes[f"/company/{number}/filing-history"]["items"]
        accounts = sorted(
            (f for f in items if f["type"] == "AA"), key=lambda f: f["date"], reverse=True
        )
        return [f["links"]["document_metadata"].rsplit("/", 1)[-1] for f in accounts]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/document/"):
            return self._document(path)
        body = self.routes.get(path)
        if body is None or body == {}:
            return httpx.Response(404, json={"errors": [{"error": "not-found"}]})
        # Fixtures hold one page; later pages come back empty, ending pagination.
        if int(request.url.params.get("start_index", 0)) > 0:
            body = {**body, "items": []}
        return httpx.Response(200, json=body)

    def _document(self, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")  # document/{id}[/content]
        doc_id = parts[1]
        if doc_id not in self.documents:
            return httpx.Response(404, json={"error": "not-found"})
        content = self.documents[doc_id]
        if len(parts) == 3:
            return httpx.Response(200, content=content or b"%PDF-1.7")
        resources = {"application/xhtml+xml": {}} if content else {"application/pdf": {}}
        return httpx.Response(200, json={"resources": resources, "category": "accounts"})

    def client(self) -> CompaniesHouseClient:
        async def no_sleep(_: float) -> None:
            return None

        return CompaniesHouseClient(
            "test-key",
            transport=httpx.MockTransport(self.handler),
            limiter=RateLimiter(sleep=no_sleep),
            retry=RetryPolicy(max_attempts=1),
            sleep=no_sleep,
        )


def first_officer_id(officers_page: dict[str, Any]) -> str:
    officers = [ApiOfficer.model_validate(o) for o in officers_page["items"]]
    return next(
        o.officer_id for o in officers if o.officer_id and "secretary" not in o.officer_role
    )
