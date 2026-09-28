"""Record real Companies House responses as test fixtures.

    uv run python scripts/record_ch_fixtures.py

Uses the live API (needs COMPANIES_HOUSE_API_KEY). Re-run when you want fresher
fixtures; tests only assert on stable fields.
"""

import asyncio
import json
from pathlib import Path

import httpx

from ukco.companies_house.client import API_BASE_URL, DOCUMENT_API_BASE_URL
from ukco.config import get_settings

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "companies_house"
GREGGS = "00502851"
# Public-register data, but no need to republish personal details in a public repo.
REDACT_OFFICER_FIELDS = ("date_of_birth", "address")


async def main() -> None:
    key = get_settings().companies_house_api_key
    assert key, "Set COMPANIES_HOUSE_API_KEY in .env"
    auth = (key.get_secret_value().strip(), "")
    async with httpx.AsyncClient(auth=auth, timeout=30) as http:

        async def save(name: str, url: str, **params: object) -> dict:
            r = await http.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if name.startswith("officers"):
                for item in data.get("items", []):
                    for field in REDACT_OFFICER_FIELDS:
                        item.pop(field, None)
            (OUT / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
            print(f"saved {name}.json")
            return data

        OUT.mkdir(parents=True, exist_ok=True)
        await save(
            "search_greggs", f"{API_BASE_URL}/search/companies", q="greggs", items_per_page=5
        )
        await save("company_greggs", f"{API_BASE_URL}/company/{GREGGS}")
        await save(
            "officers_greggs", f"{API_BASE_URL}/company/{GREGGS}/officers", items_per_page=10
        )
        filings = await save(
            "filing_history_greggs_accounts",
            f"{API_BASE_URL}/company/{GREGGS}/filing-history",
            category="accounts",
            items_per_page=5,
        )
        doc_url = filings["items"][0]["links"]["document_metadata"]
        doc_id = doc_url.rstrip("/").rsplit("/", 1)[-1]
        await save("document_metadata_greggs", f"{DOCUMENT_API_BASE_URL}/document/{doc_id}")


if __name__ == "__main__":
    asyncio.run(main())
