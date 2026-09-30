"""KYB endpoints: PSCs, officer network, charges, insolvency, advanced search, evidence hook.

Fixtures are real (pseudonymised) responses for two small companies incorporated March 2021:
13237895 (active, has a PSC) and 13236560 (members' voluntary liquidation).
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.conftest import FIXTURES
from ukco.companies_house import CompaniesHouseClient, DiskCache, RateLimiter, RetryPolicy

pytestmark = pytest.mark.anyio

KYB = FIXTURES / "companies_house" / "kyb"
ACTIVE, LIQUIDATION = "13237895", "13236560"


def kyb(name: str) -> Any:
    return json.loads((KYB / f"{name}.json").read_text())


def routes(label: str, number: str) -> dict[str, Any]:
    base = f"/company/{number}"
    return {
        base: kyb(f"{label}_profile"),
        f"{base}/officers": kyb(f"{label}_officers"),
        f"{base}/persons-with-significant-control": kyb(
            f"{label}_persons_with_significant_control"
        ),
        f"{base}/persons-with-significant-control-statements": kyb(
            f"{label}_persons_with_significant_control_statements"
        ),
        f"{base}/charges": kyb(f"{label}_charges"),
        f"{base}/insolvency": kyb(f"{label}_insolvency"),
    }


def fixture_client(extra: dict[str, Any] | None = None, **kwargs: Any) -> CompaniesHouseClient:
    table = {**routes("active", ACTIVE), **routes("liquidation", LIQUIDATION), **(extra or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        body = table.get(request.url.path)
        if body is None or body == {}:  # `{}` fixtures record "none on file" (a 404)
            return httpx.Response(404, json={"errors": [{"error": "not-found"}]})
        return httpx.Response(200, json=body)

    async def no_sleep(_: float) -> None:
        return None

    return CompaniesHouseClient(
        "test-key",
        transport=httpx.MockTransport(handler),
        limiter=RateLimiter(sleep=no_sleep),
        retry=RetryPolicy(max_attempts=1),
        sleep=no_sleep,
        **kwargs,
    )


async def test_profile_exposes_filing_deadlines() -> None:
    async with fixture_client() as client:
        profile = await client.get_company(ACTIVE)

    assert profile.accounts and profile.accounts.next_due
    assert profile.confirmation_statement and profile.confirmation_statement.next_due
    assert profile.accounts.overdue in (True, False)
    assert profile.date_of_creation in (dt.date(2021, 3, 1), dt.date(2021, 3, 2))


async def test_pscs_have_stable_ids_and_dates() -> None:
    async with fixture_client() as client:
        pscs = await client.list_pscs(ACTIVE)

    assert pscs.items
    psc = pscs.items[0]
    assert psc.psc_id and psc.kind.startswith("individual")
    assert psc.notified_on is not None
    assert psc.is_active
    assert any("ownership-of-shares" in n for n in psc.natures_of_control)


async def test_missing_registers_are_empty_not_errors() -> None:
    async with fixture_client() as client:
        insolvency = await client.get_insolvency(ACTIVE)
        statements = await client.list_psc_statements(ACTIVE)
        charges = await client.list_charges(ACTIVE)

    assert insolvency.cases == []
    assert isinstance(statements.items, list)
    assert isinstance(charges.items, list)


async def test_liquidation_case_is_parsed() -> None:
    async with fixture_client() as client:
        insolvency = await client.get_insolvency(LIQUIDATION)
        profile = await client.get_company(LIQUIDATION)

    assert profile.company_status == "liquidation"
    # A members' voluntary liquidation is a *solvent* wind-down: eval labels must not
    # count it as a bad outcome (ADR-004).
    assert [c.type for c in insolvency.cases] == ["members-voluntary-liquidation"]


async def test_officer_network_parses_linked_company_status() -> None:
    officer_id = "SyntheticOfficer0001"
    page = kyb("active_officer_appointments")
    async with fixture_client({f"/officers/{officer_id}/appointments": page}) as client:
        network = await client.get_officer_network(officer_id)

    assert network.appointments
    assert all(a.appointed_to.company_number for a in network.appointments)
    assert all(a.appointed_to.company_status for a in network.appointments)
    assert not network.truncated


async def test_officer_network_pages_and_truncates_professional_officers() -> None:
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start_index"])
        requested.append(start)
        items = [
            {
                "appointed_to": {"company_number": f"{i:08d}", "company_status": "dissolved"},
                "officer_role": "director",
            }
            for i in range(start, start + 50)
        ]
        return httpx.Response(
            200, json={"items": items, "total_results": 3000, "is_corporate_officer": True}
        )

    async def no_sleep(_: float) -> None:
        return None

    client = CompaniesHouseClient(
        "k", transport=httpx.MockTransport(handler), limiter=RateLimiter(sleep=no_sleep)
    )
    async with client:
        network = await client.get_officer_network("NomineeOfficer0001", max_items=200)

    assert len(network.appointments) == 200
    assert network.truncated and network.total_results == 3000
    assert network.is_corporate_officer
    assert requested == [0, 50, 100, 150]  # 4 calls, not 60


async def test_officer_id_is_validated() -> None:
    async with fixture_client() as client:
        with pytest.raises(ValueError):
            await client.get_officer_network("../../company/00000000")


async def test_advanced_search_encodes_filters() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=kyb("advanced_search_2021_03"))

    client = CompaniesHouseClient("k", transport=httpx.MockTransport(handler))
    async with client:
        result = await client.advanced_search(
            incorporated_from=dt.date(2021, 3, 1),
            incorporated_to=dt.date(2021, 3, 2),
            company_status=["active", "dissolved"],
            company_type=["ltd"],
            size=10,
        )

    params = seen[0].url.params
    assert params["incorporated_from"] == "2021-03-01"
    assert params["company_status"] == "active,dissolved"
    assert params["company_type"] == "ltd"
    assert result.hits > 0 and result.items
    assert all(i.date_of_creation for i in result.items)


async def test_observer_sees_every_response_including_cache_hits_and_absences(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, Any]] = []
    cache = DiskCache(tmp_path)
    client = fixture_client(
        cache=cache, on_json=lambda path, _params, body: seen.append((path, body))
    )
    async with client:
        await client.get_company(ACTIVE)
        await client.get_company(ACTIVE)  # served from cache: still evidence
        await client.get_insolvency(ACTIVE)  # 404: recorded as "none on file"

    paths = [p for p, _ in seen]
    assert paths == [f"/company/{ACTIVE}", f"/company/{ACTIVE}", f"/company/{ACTIVE}/insolvency"]
    assert seen[2][1] == {}


async def test_advanced_search_with_no_matches_is_empty() -> None:
    client = CompaniesHouseClient(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    )
    async with client:
        result = await client.advanced_search(incorporated_from=dt.date(2021, 3, 1), size=10)
    assert result.items == [] and result.hits == 0
