"""Record real responses for the KYB endpoints as pseudonymised test fixtures.

    uv run python scripts/record_kyb_fixtures.py

Samples small private companies incorporated on 1-2 March 2021 (the KYB-typical profile),
picks one active company with PSCs and one in liquidation, and saves profile, officers,
PSCs, PSC statements, charges, insolvency, filing history and one officer's appointments.

Small-company officers and PSCs are private individuals, so every person's name becomes a
stable pseudonym ("PERSON 1") and birth dates / addresses are dropped before saving.
Company names and opaque Companies House ids are kept (tests rely on them).
"""

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import Any

from ukco.companies_house import CompaniesHouseClient
from ukco.config import get_settings

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "companies_house" / "kyb"
DROP = {"date_of_birth", "address", "name_elements", "principal_office_address", "identification"}


ADDRESS_TEXT = {"old_address", "new_address"}  # AD01 description_values


def is_person_name(key: str) -> bool:
    """`name`, plus any `*_name` that isn't a company's: filing descriptions carry people's
    names as `officer_name`, `psc_name`, … and new filing types may add more."""
    return key == "name" or (key.endswith("_name") and "company" not in key)


class Pseudonymiser:
    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.addresses: dict[str, str] = {}

    def _address(self, key: str) -> str:
        # Stable, so "moved back to a previous address" is still visible in tests.
        return self.addresses.setdefault(key, f"ADDRESS {len(self.addresses) + 1}")

    def __call__(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self(v) for v in value]
        if not isinstance(value, dict):
            return value
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in DROP:
                continue
            if is_person_name(k) and isinstance(v, str):
                out[k] = self.names.setdefault(v, f"PERSON {len(self.names) + 1}")
            elif k in ADDRESS_TEXT and isinstance(v, str):
                out[k] = self._address(v)
            elif k == "registered_office_address" and isinstance(v, dict):
                # Small companies often register at a director's home address.
                full = json.dumps(v, sort_keys=True)
                out[k] = {"address_line_1": self._address(full), "country": v.get("country")}
            else:
                out[k] = self(v)
        return out


async def main() -> None:
    key = get_settings().companies_house_api_key
    assert key, "Set COMPANIES_HOUSE_API_KEY in .env"
    OUT.mkdir(parents=True, exist_ok=True)
    pseudo = Pseudonymiser()
    saved: dict[str, Any] = {}

    def observe(path: str, params: dict[str, Any], body: Any) -> None:
        saved[path] = body

    async with CompaniesHouseClient(key.get_secret_value().strip(), on_json=observe) as client:
        sample = await client.advanced_search(
            incorporated_from=dt.date(2021, 3, 1),
            incorporated_to=dt.date(2021, 3, 2),
            company_type=["ltd"],
            size=60,
        )
        write("advanced_search_2021_03", saved["/advanced-search/companies"], pseudo, trim=10)

        active = liquidating = None
        for item in sample.items:
            if (
                item.company_status == "active"
                and not active
                and (await client.list_pscs(item.company_number)).items
            ):
                active = item.company_number
            if item.company_status == "liquidation" and not liquidating:
                liquidating = item.company_number
            if active and liquidating:
                break
        assert active and liquidating, "sample had no suitable companies; widen the date range"

        for label, number in (("active", active), ("liquidation", liquidating)):
            saved.clear()
            await client.get_company(number)
            officers = [o async for o in client.iter_officers(number)]
            await client.list_pscs(number)
            await client.list_psc_statements(number)
            await client.list_charges(number)
            await client.get_insolvency(number)
            _ = [f async for f in client.iter_filing_history(number)]
            for path, body in saved.items():
                endpoint = path.removeprefix(f"/company/{number}").strip("/") or "profile"
                write(f"{label}_{endpoint.replace('/', '_').replace('-', '_')}", body, pseudo)
            if label == "active" and officers and officers[0].officer_id:
                saved.clear()
                await client.get_officer_network(officers[0].officer_id)
                first_page = next(iter(saved.values()))
                write(f"{label}_officer_appointments", first_page, pseudo)

    print(f"active={active} liquidation={liquidating}")
    (OUT / "README.md").write_text(
        "# KYB fixtures\n\nReal responses recorded by `scripts/record_kyb_fixtures.py` for two\n"
        f"small companies incorporated March 2021: `{active}` (active, has PSCs) and\n"
        f"`{liquidating}` (in liquidation). People's names and addresses (including\n"
        "registered-office addresses) are stable pseudonyms; birth dates removed.\n"
        "Missing registers (e.g. no insolvency) are saved as `{}`.\n"
    )


def write(name: str, body: Any, pseudo: Pseudonymiser, trim: int | None = None) -> None:
    body = pseudo(body)
    if trim and isinstance(body, dict) and "items" in body:
        body = {**body, "items": body["items"][:trim]}
    (OUT / f"{name}.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    print(f"saved kyb/{name}.json")


if __name__ == "__main__":
    asyncio.run(main())
