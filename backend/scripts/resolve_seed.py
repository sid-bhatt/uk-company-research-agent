"""Resolve seed company names to Companies House numbers.

    uv run python scripts/resolve_seed.py

Reads seed/companies_input.csv (group, query) and writes seed/companies.csv.
Only exact name matches (after normalising "PLC"/"LIMITED"/punctuation) are
accepted automatically; anything else is marked `unresolved` with the top
candidates, for a human to decide. Rows already resolved in companies.csv are
kept as-is, so manual fixes survive re-runs.
"""

import asyncio
import csv
import re
from pathlib import Path

from ukco.companies_house import client_from_settings

SEED = Path(__file__).resolve().parents[1] / "seed"
FIELDS = ["group", "query", "company_number", "name", "status", "resolution", "note"]

_SUFFIXES = [
    (r"\bPUBLIC LIMITED COMPANY\b", "PLC"),
    (r"\bP\.?\s?L\.?\s?C\.?", "PLC"),
    (r"\bLIMITED\b", "LTD"),
    (r"&", " AND "),
    (r"^THE\s+", ""),
]


def normalise(name: str) -> str:
    n = name.upper()
    for pattern, repl in _SUFFIXES:
        n = re.sub(pattern, repl, n)
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    return " ".join(n.split())


def load_existing() -> dict[str, dict[str, str]]:
    path = SEED / "companies.csv"
    if not path.exists():
        return {}
    with path.open() as f:
        return {row["query"]: row for row in csv.DictReader(f)}


async def main() -> None:
    with (SEED / "companies_input.csv").open() as f:
        wanted = list(csv.DictReader(f))
    existing = load_existing()
    rows: list[dict[str, str]] = []

    async with client_from_settings() as client:
        for item in wanted:
            query, group = item["query"], item["group"]
            prior = existing.get(query)
            if prior and prior["resolution"] in {"exact", "manual", "not_uk_registered"}:
                rows.append({**prior, "group": group})
                continue

            result = await client.search_companies(query, limit=20)
            target = normalise(query)
            exact = [c for c in result.items if normalise(c.title) == target]
            exact.sort(key=lambda c: c.company_status != "active")  # prefer active

            if exact:
                c = exact[0]
                row = {
                    "company_number": c.company_number,
                    "name": c.title,
                    "status": c.company_status or "",
                    "resolution": "exact",
                    "note": "",
                }
            else:
                top = "; ".join(f"{c.company_number} {c.title}" for c in result.items[:3])
                row = {
                    "company_number": "",
                    "name": "",
                    "status": "",
                    "resolution": "unresolved",
                    "note": f"candidates: {top}",
                }
            rows.append({"group": group, "query": query, **row})

    with (SEED / "companies.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["resolution"]] = counts.get(r["resolution"], 0) + 1
    print(f"wrote {len(rows)} rows: {counts}")
    for r in rows:
        if r["resolution"] == "unresolved":
            print(f"  ? {r['query']}  →  {r['note']}")


if __name__ == "__main__":
    asyncio.run(main())
