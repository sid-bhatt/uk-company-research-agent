"""Ingest company metadata: profile, officers, filing history.

Idempotent and incremental by construction:
- every write is an upsert on a Companies House natural key
- an existing row is only rewritten when a tracked column actually changed, so
  `updated_at` means "data changed", and stats report inserted/updated/unchanged
- filing documents worth parsing are queued as `documents` rows (status=pending)
  for the Day 4 parse step; re-queuing never resets a document's progress
"""

import datetime as dt
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine, Table, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert

from ukco.companies_house import CompaniesHouseClient, CompaniesHouseError
from ukco.companies_house.models import CompanyProfile, FilingHistoryItem, Officer
from ukco.db.models import Company, Document, Filing, IngestionRun
from ukco.db.models import Officer as OfficerRow

log = logging.getLogger(__name__)

# Which filings get their documents downloaded and parsed (Day 4).
DOCUMENT_CATEGORIES = ("accounts",)
DOCUMENT_LOOKBACK_YEARS = 3
# How far back to fetch filing history metadata. Enough for "what changed recently"
# questions without paging through decades of share-buyback notices.
FILING_HISTORY_YEARS = 10


@dataclass
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def add(self, other: "UpsertCounts") -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged


@dataclass
class CompanyResult:
    company_number: str
    companies: UpsertCounts = field(default_factory=UpsertCounts)
    officers: UpsertCounts = field(default_factory=UpsertCounts)
    filings: UpsertCounts = field(default_factory=UpsertCounts)
    documents_queued: int = 0
    skipped_officers: int = 0  # no appointment id → no natural key
    error: str | None = None


def upsert(
    conn: Connection,
    table: Table,
    rows: Sequence[dict[str, Any]],
    key: Sequence[str],
    tracked: Sequence[str],
) -> UpsertCounts:
    """INSERT ... ON CONFLICT DO UPDATE, but only when a tracked column changed."""
    if not rows:
        return UpsertCounts()
    stmt = insert(table).values(list(rows))
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=list(key),
        set_={**{c: excluded[c] for c in tracked}, "updated_at": func.now()},
        where=or_(*(table.c[c].is_distinct_from(excluded[c]) for c in tracked)),
    ).returning(text("(xmax = 0) AS inserted"))  # xmax = 0 ⇔ row was freshly inserted
    written = [r.inserted for r in conn.execute(stmt)]
    inserted = sum(written)
    return UpsertCounts(
        inserted=inserted, updated=len(written) - inserted, unchanged=len(rows) - len(written)
    )


def _company_row(p: CompanyProfile, raw: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    return {
        "company_number": p.company_number,
        "name": p.company_name,
        "status": p.company_status,
        "type": p.type,
        "jurisdiction": p.jurisdiction,
        "date_of_creation": p.date_of_creation,
        "date_of_cessation": p.date_of_cessation,
        "registered_office": (
            p.registered_office_address.one_line() if p.registered_office_address else None
        ),
        "sic_codes": list(p.sic_codes),
        "has_insolvency_history": p.has_insolvency_history,
        "raw": raw,
        "fetched_at": now,
    }


def _officer_row(company_number: str, o: Officer, now: dt.datetime) -> dict[str, Any] | None:
    if not o.appointment_id:
        return None
    return {
        "appointment_id": o.appointment_id,
        "company_number": company_number,
        "officer_id": o.officer_id,
        "name": o.name,
        "role": o.officer_role,
        "appointed_on": o.appointed_on,
        "resigned_on": o.resigned_on,
        "nationality": o.nationality,
        "occupation": o.occupation,
        "country_of_residence": o.country_of_residence,
        "fetched_at": now,
    }


def _filing_row(company_number: str, f: FilingHistoryItem, now: dt.datetime) -> dict[str, Any]:
    return {
        "company_number": company_number,
        "transaction_id": f.transaction_id,
        "category": f.category,
        "type": f.type,
        "date": f.date,
        "description": f.description,
        "description_values": dict(f.description_values),
        "pages": f.pages,
        "paper_filed": f.paper_filed,
        "document_id": f.document_id,
        "fetched_at": now,
    }


def wants_document(f: FilingHistoryItem, today: dt.date) -> bool:
    if not f.document_id or f.category not in DOCUMENT_CATEGORIES or not f.date:
        return False
    return f.date >= today.replace(year=today.year - DOCUMENT_LOOKBACK_YEARS)


COMPANY_TRACKED = [
    "name", "status", "type", "jurisdiction", "date_of_creation", "date_of_cessation",
    "registered_office", "sic_codes", "has_insolvency_history", "raw",
]  # fmt: skip
OFFICER_TRACKED = [
    "officer_id", "name", "role", "appointed_on", "resigned_on", "nationality",
    "occupation", "country_of_residence",
]  # fmt: skip
FILING_TRACKED = [
    "category", "type", "date", "description", "description_values", "pages",
    "paper_filed", "document_id",
]  # fmt: skip


async def ingest_company(
    client: CompaniesHouseClient,
    engine: Engine,
    company_number: str,
    *,
    today: dt.date | None = None,
) -> CompanyResult:
    """Fetch everything for one company, then write it in a single transaction."""
    today = today or dt.date.today()
    now = dt.datetime.now(dt.UTC)
    result = CompanyResult(company_number)

    # Fetch first (network), then write (DB): no transaction held open across API calls.
    raw = await client.get_company_raw(company_number)
    profile = CompanyProfile.model_validate(raw)
    officers = [o async for o in client.iter_officers(profile.company_number)]
    since = today.replace(year=today.year - FILING_HISTORY_YEARS)
    filings = [f async for f in client.iter_filing_history(profile.company_number, since=since)]

    officer_rows = [r for o in officers if (r := _officer_row(profile.company_number, o, now))]
    result.skipped_officers = len(officers) - len(officer_rows)

    with engine.begin() as conn:
        result.companies = upsert(
            conn,
            Company.__table__,  # type: ignore[arg-type]
            [_company_row(profile, raw, now)],
            key=["company_number"],
            tracked=COMPANY_TRACKED,
        )
        result.officers = upsert(
            conn,
            OfficerRow.__table__,  # type: ignore[arg-type]
            _dedupe(officer_rows, "appointment_id"),
            key=["appointment_id"],
            tracked=OFFICER_TRACKED,
        )
        filing_rows = _dedupe(
            [_filing_row(profile.company_number, f, now) for f in filings], "transaction_id"
        )
        for batch in _batches(filing_rows, 500):
            result.filings.add(
                upsert(
                    conn,
                    Filing.__table__,  # type: ignore[arg-type]
                    batch,
                    key=["company_number", "transaction_id"],
                    tracked=FILING_TRACKED,
                )
            )
        result.documents_queued = _queue_documents(
            conn, profile.company_number, [f for f in filings if wants_document(f, today)]
        )
    return result


def _queue_documents(
    conn: Connection, company_number: str, filings: Sequence[FilingHistoryItem]
) -> int:
    if not filings:
        return 0
    ids = dict(
        conn.execute(
            select(Filing.transaction_id, Filing.id).where(
                Filing.company_number == company_number,
                Filing.transaction_id.in_([f.transaction_id for f in filings]),
            )
        ).all()
    )
    rows = [
        {
            "document_id": f.document_id,
            "filing_id": ids[f.transaction_id],
            "company_number": company_number,
        }
        for f in filings
    ]
    stmt = (
        insert(Document)
        .values(_dedupe(rows, "document_id"))
        .on_conflict_do_nothing(index_elements=["document_id"])  # never reset progress
        .returning(Document.document_id)  # rowcount is unreliable for multi-row inserts
    )
    return len(conn.execute(stmt).all())


def _dedupe(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """The API occasionally repeats an item across pages; Postgres rejects duplicate
    keys within one INSERT ... ON CONFLICT statement, so keep the last occurrence."""
    return list({r[key]: r for r in rows}.values())


def _batches(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


async def ingest_companies(
    client: CompaniesHouseClient,
    engine: Engine,
    company_numbers: Sequence[str],
    *,
    trigger: str = "cli",
) -> tuple[int, list[CompanyResult]]:
    """Ingest many companies, recording the run. One company failing doesn't stop the rest."""
    with engine.begin() as conn:
        run_id = conn.execute(
            insert(IngestionRun)
            .values(trigger=trigger, params={"companies": len(company_numbers)})
            .returning(IngestionRun.id)
        ).scalar_one()

    results: list[CompanyResult] = []
    for number in company_numbers:
        try:
            res = await ingest_company(client, engine, number)
        except (CompaniesHouseError, ValueError) as exc:
            log.warning("company %s failed: %s", number, exc)
            res = CompanyResult(number, error=str(exc))
        results.append(res)
        log.info(
            "%s officers+%d filings+%d docs+%d",
            number,
            res.officers.inserted,
            res.filings.inserted,
            res.documents_queued,
        )

    failed = [r for r in results if r.error]
    status = "failed" if len(failed) == len(results) else "partial" if failed else "succeeded"
    with engine.begin() as conn:
        conn.execute(
            IngestionRun.__table__.update()  # type: ignore[attr-defined]
            .where(IngestionRun.id == run_id)
            .values(
                status=status,
                stats=summarise(results),
                error="; ".join(f"{r.company_number}: {r.error}" for r in failed)[:4000] or None,
                finished_at=func.now(),
            )
        )
    return run_id, results


def summarise(results: Sequence[CompanyResult]) -> dict[str, Any]:
    totals: dict[str, Any] = {"companies_ok": 0, "companies_failed": 0, "documents_queued": 0}
    for kind in ("companies", "officers", "filings"):
        totals[kind] = {"inserted": 0, "updated": 0, "unchanged": 0}
    for r in results:
        totals["companies_failed" if r.error else "companies_ok"] += 1
        totals["documents_queued"] += r.documents_queued
        for kind in ("companies", "officers", "filings"):
            counts: UpsertCounts = getattr(r, kind)
            for k in ("inserted", "updated", "unchanged"):
                totals[kind][k] += getattr(counts, k)
    return totals
