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

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = self.routes.get(request.url.path)
        if body is None or body == {}:
            return httpx.Response(404, json={"errors": [{"error": "not-found"}]})
        # Fixtures hold one page; later pages come back empty, ending pagination.
        if int(request.url.params.get("start_index", 0)) > 0:
            body = {**body, "items": []}
        return httpx.Response(200, json=body)

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
