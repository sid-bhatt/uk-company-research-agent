"""Fetch one company's full register data (a *bundle*) and persist it.

    bundle  = fetch_bundle(client, "13237895")      # network I/O only
    result  = persist_bundle(conn, bundle)            # one DB transaction
    result.evidence.require("filing", n, txn_id)      # → Citation for a rule to use

Idempotent and incremental by construction:
- every register write is an upsert on a Companies House natural key, and a row is only
  rewritten when a tracked column changed (stats: inserted / updated / unchanged)
- identical API responses share one `source_snapshots` row
- queued documents never have their processing status reset
"""

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine, Table, and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert

from ukco.companies_house import CompaniesHouseClient, CompaniesHouseError
from ukco.companies_house.models import (
    ChargeList,
    CompanyProfile,
    FilingHistoryItem,
    Insolvency,
    Officer,
    OfficerNetwork,
    Psc,
    PscStatement,
)
from ukco.db.models import (
    Appointment,
    Company,
    Document,
    Filing,
    IngestionRun,
    SourceSnapshot,
)
from ukco.db.models import Officer as PersonRow
from ukco.db.models import Psc as PscRow
from ukco.db.models import (
    PscStatement as PscStatementRow,
)
from ukco.evidence import EvidenceIndex, StoredResponse

log = logging.getLogger(__name__)

FILING_HISTORY_YEARS = 10  # enough for KYB; Barclays alone has 4,500+ older filings
DOCUMENT_CATEGORIES = ("accounts",)
DOCUMENT_LOOKBACK_YEARS = 3
NETWORK_WINDOW_YEARS = 3  # officers resigned longer ago than this aren't network-checked
MAX_NETWORK_OFFICERS = 25  # bounds rate-limit cost for large boards
MAX_APPOINTMENTS_PER_OFFICER = 200


# --- Bundle (fetch) ------------------------------------------------------------------


@dataclass(frozen=True)
class RawResponse:
    path: str
    params: dict[str, Any]
    body: Any
    fetched_at: dt.datetime


@dataclass
class CompanyBundle:
    company_number: str
    fetched_at: dt.datetime
    raw_profile: dict[str, Any]
    profile: CompanyProfile
    officers: list[Officer]
    pscs: list[Psc]
    psc_statements: list[PscStatement]
    charges: ChargeList
    insolvency: Insolvency
    filings: list[FilingHistoryItem]
    networks: dict[str, OfficerNetwork] = field(default_factory=dict)
    network_errors: dict[str, str] = field(default_factory=dict)
    network_skipped: list[str] = field(default_factory=list)  # over MAX_NETWORK_OFFICERS
    responses: list[RawResponse] = field(default_factory=list)


def network_candidates(officers: Iterable[Officer], today: dt.date) -> list[str]:
    """Officer ids worth a network check: natural-person directors/members who are active
    or resigned within NETWORK_WINDOW_YEARS. Secretaries and corporate officers are
    excluded (formation agents and company-secretarial firms sit on thousands of boards)."""
    cutoff = today.replace(year=today.year - NETWORK_WINDOW_YEARS)
    eligible = [
        o
        for o in officers
        if o.officer_id
        and not o.officer_role.startswith("corporate")
        and "secretary" not in o.officer_role
        and not (o.resigned_on and o.resigned_on < cutoff)
    ]
    # Active officers first, then the most recently resigned.
    eligible.sort(key=lambda o: (o.resigned_on is not None, -(o.resigned_on or today).toordinal()))
    return list(dict.fromkeys(o.officer_id for o in eligible if o.officer_id))


async def fetch_bundle(
    client: CompaniesHouseClient,
    company_number: str,
    *,
    network: bool = True,
    today: dt.date | None = None,
) -> CompanyBundle:
    """Every register a KYB report needs, plus each response verbatim for evidence."""
    today = today or dt.date.today()
    responses: list[RawResponse] = []
    previous_observer = client.on_json

    def observe(path: str, params: Any, body: Any) -> None:
        responses.append(RawResponse(path, dict(params), body, dt.datetime.now(dt.UTC)))
        if previous_observer:
            previous_observer(path, params, body)

    client.on_json = observe
    try:
        raw_profile = await client.get_company_raw(company_number)
        profile = CompanyProfile.model_validate(raw_profile)
        number = profile.company_number
        officers = [o async for o in client.iter_officers(number)]
        pscs = await client.list_pscs(number)
        statements = await client.list_psc_statements(number)
        charges = await client.list_charges(number)
        insolvency = await client.get_insolvency(number)
        since = today.replace(year=today.year - FILING_HISTORY_YEARS)
        filings = [f async for f in client.iter_filing_history(number, since=since)]

        bundle = CompanyBundle(
            company_number=number,
            fetched_at=dt.datetime.now(dt.UTC),
            raw_profile=raw_profile,
            profile=profile,
            officers=officers,
            pscs=list(pscs.items),
            psc_statements=list(statements.items),
            charges=charges,
            insolvency=insolvency,
            filings=filings,
        )
        if network:
            candidates = network_candidates(officers, today)
            bundle.network_skipped = candidates[MAX_NETWORK_OFFICERS:]
            for officer_id in candidates[:MAX_NETWORK_OFFICERS]:
                try:
                    bundle.networks[officer_id] = await client.get_officer_network(
                        officer_id, max_items=MAX_APPOINTMENTS_PER_OFFICER
                    )
                except CompaniesHouseError as exc:  # one officer failing isn't fatal
                    bundle.network_errors[officer_id] = str(exc)
        bundle.responses = responses
        return bundle
    finally:
        client.on_json = previous_observer


# --- Persist -------------------------------------------------------------------------


@dataclass
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def add(self, other: "UpsertCounts") -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged


COUNTED = ("companies", "appointments", "pscs", "filings")


@dataclass
class CompanyResult:
    company_number: str
    companies: UpsertCounts = field(default_factory=UpsertCounts)
    appointments: UpsertCounts = field(default_factory=UpsertCounts)
    pscs: UpsertCounts = field(default_factory=UpsertCounts)
    filings: UpsertCounts = field(default_factory=UpsertCounts)
    documents_queued: int = 0
    network_officers: int = 0
    network_truncated: int = 0
    network_errors: int = 0
    network_skipped: int = 0
    linked_companies: int = 0  # companies seen only via officers' appointments
    snapshot_ids: list[int] = field(default_factory=list)
    evidence: EvidenceIndex = field(default_factory=EvidenceIndex)
    error: str | None = None


def upsert(
    conn: Connection,
    table: Table,
    rows: Sequence[dict[str, Any]],
    *,
    tracked: Sequence[str],
    key: Sequence[str] | None = None,
    constraint: str | None = None,
    also_set: Sequence[str] = (),
) -> UpsertCounts:
    """INSERT ... ON CONFLICT DO UPDATE, but only when a tracked column changed.

    `also_set` columns are written alongside a real change but never cause one.
    """
    if not rows:
        return UpsertCounts()
    stmt = insert(table).values(list(rows))
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=list(key) if key else None,
        constraint=constraint,
        set_={**{c: excluded[c] for c in (*tracked, *also_set)}, "updated_at": func.now()},
        where=or_(*(table.c[c].is_distinct_from(excluded[c]) for c in tracked)),
    ).returning(text("(xmax = 0) AS inserted"))  # xmax = 0 ⇔ row was freshly inserted
    written = [r.inserted for r in conn.execute(stmt)]
    inserted = sum(written)
    return UpsertCounts(inserted, len(written) - inserted, len(rows) - len(written))


def _dedupe(rows: list[dict[str, Any]], *key: str) -> list[dict[str, Any]]:
    """Postgres rejects duplicate keys within one INSERT ... ON CONFLICT; keep the last."""
    return list({tuple(r[k] for k in key): r for r in rows}.values())


def _batches(rows: list[dict[str, Any]], size: int = 500) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _content_key(r: RawResponse) -> str:
    blob = json.dumps([r.path, r.params, r.body], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def store_snapshots(conn: Connection, responses: Sequence[RawResponse]) -> list[int]:
    """Store each response once; identical responses share a row. Returns ids in order."""
    ids: list[int] = []
    for r in responses:
        stmt = (
            insert(SourceSnapshot)
            .values(content_key=_content_key(r), path=r.path, params=r.params, body=r.body)
            .on_conflict_do_update(
                index_elements=["content_key"], set_={"last_seen_at": func.now()}
            )
            .returning(SourceSnapshot.id)
        )
        ids.append(conn.execute(stmt).scalar_one())
    return ids


COMPANY_TRACKED = [
    "name", "status", "type", "jurisdiction", "date_of_creation", "date_of_cessation",
    "registered_office", "sic_codes", "has_insolvency_history", "has_been_liquidated",
    "registered_office_is_in_dispute", "undeliverable_registered_office_address",
    "accounts_next_due", "accounts_overdue", "last_accounts_made_up_to",
    "confirmation_statement_next_due", "confirmation_statement_overdue", "raw",
]  # fmt: skip
COMPANY_SIDE_APPOINTMENT_TRACKED = [
    "resigned_on", "appointment_id", "name", "nationality", "occupation",
    "country_of_residence",
]  # fmt: skip
NETWORK_APPOINTMENT_TRACKED = ["resigned_on"]  # the company-side row is richer; don't clobber
PERSON_TRACKED = ["name", "is_corporate", "total_appointments", "appointments_truncated"]
PSC_TRACKED = [
    "kind", "name", "natures_of_control", "notified_on", "ceased_on", "nationality",
    "country_of_residence",
]  # fmt: skip
FILING_TRACKED = [
    "category", "type", "date", "description", "description_values", "pages",
    "paper_filed", "document_id",
]  # fmt: skip
APPOINTMENT_EDGE = "uq_appointments_edge"


def _company_row(b: CompanyBundle) -> dict[str, Any]:
    p = b.profile
    accounts, cs = p.accounts, p.confirmation_statement
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
        "has_been_liquidated": p.has_been_liquidated,
        "registered_office_is_in_dispute": p.registered_office_is_in_dispute,
        "undeliverable_registered_office_address": p.undeliverable_registered_office_address,
        "accounts_next_due": accounts.next_due if accounts else None,
        "accounts_overdue": accounts.overdue if accounts else None,
        "last_accounts_made_up_to": (
            accounts.last_accounts.made_up_to if accounts and accounts.last_accounts else None
        ),
        "confirmation_statement_next_due": cs.next_due if cs else None,
        "confirmation_statement_overdue": cs.overdue if cs else None,
        "raw": b.raw_profile,
        "profile_fetched_at": b.fetched_at,
    }


def _upsert_stub_companies(conn: Connection, rows: list[dict[str, Any]]) -> None:
    """Companies seen only through an officer's appointments. Never overwrite a full
    profile with the thinner stub data."""
    if not rows:
        return
    table = Company.__table__
    stmt = insert(Company).values(_dedupe(rows, "company_number"))
    stmt = stmt.on_conflict_do_update(
        index_elements=["company_number"],
        set_={"name": stmt.excluded.name, "status": stmt.excluded.status, "updated_at": func.now()},
        where=and_(
            table.c.profile_fetched_at.is_(None),  # type: ignore[attr-defined]
            or_(
                table.c.name.is_distinct_from(stmt.excluded.name),  # type: ignore[attr-defined]
                table.c.status.is_distinct_from(stmt.excluded.status),  # type: ignore[attr-defined]
            ),
        ),
    )
    conn.execute(stmt)


def persist_bundle(
    conn: Connection, b: CompanyBundle, *, today: dt.date | None = None
) -> CompanyResult:
    today = today or dt.date.today()
    n = b.company_number
    result = CompanyResult(n)

    # Evidence first: every fact below can be cited back to these rows.
    result.snapshot_ids = store_snapshots(conn, b.responses)
    result.evidence = EvidenceIndex.build(
        StoredResponse(sid, r.path, r.params, r.body)
        for sid, r in zip(result.snapshot_ids, b.responses, strict=True)
    )

    result.companies = upsert(
        conn, Company.__table__, [_company_row(b)],  # type: ignore[arg-type]
        key=["company_number"], tracked=COMPANY_TRACKED, also_set=["profile_fetched_at"],
    )  # fmt: skip

    # People: bare rows from the company's officer list, richer rows from their networks.
    officers = [o for o in b.officers if o.officer_id]
    if officers:
        people = [{"officer_id": o.officer_id, "name": o.name} for o in officers]
        conn.execute(
            insert(PersonRow)
            .values(_dedupe(people, "officer_id"))
            .on_conflict_do_nothing(index_elements=["officer_id"])  # networks fill details
        )
    upsert(
        conn,
        PersonRow.__table__,  # type: ignore[arg-type]
        [
            {
                "officer_id": oid,
                "name": net.name,
                "is_corporate": net.is_corporate_officer,
                "total_appointments": net.total_results,
                "appointments_truncated": net.truncated,
                "appointments_fetched_at": b.fetched_at,
            }
            for oid, net in b.networks.items()
        ],
        key=["officer_id"],
        tracked=PERSON_TRACKED,
        also_set=["appointments_fetched_at"],
    )

    # Companies linked through the network must exist before their appointment edges.
    stubs = [
        {
            "company_number": a.appointed_to.company_number,
            "name": a.appointed_to.company_name,
            "status": a.appointed_to.company_status,
        }
        for net in b.networks.values()
        for a in net.appointments
        if a.appointed_to.company_number != n
    ]
    _upsert_stub_companies(conn, stubs)
    result.linked_companies = len({s["company_number"] for s in stubs})

    edge = ("company_number", "officer_id", "role", "appointed_on")
    company_side = _dedupe(
        [
            {
                "company_number": n,
                "officer_id": o.officer_id,
                "role": o.officer_role,
                "appointed_on": o.appointed_on,
                "resigned_on": o.resigned_on,
                "appointment_id": o.appointment_id,
                "name": o.name,
                "nationality": o.nationality,
                "occupation": o.occupation,
                "country_of_residence": o.country_of_residence,
            }
            for o in officers
        ],
        *edge,
    )
    network_side = _dedupe(
        [
            {
                "company_number": a.appointed_to.company_number,
                "officer_id": oid,
                "role": a.officer_role,
                "appointed_on": a.appointed_on,
                "resigned_on": a.resigned_on,
                "name": a.name,
            }
            for oid, net in b.networks.items()
            for a in net.appointments
        ],
        *edge,
    )
    appointments = Appointment.__table__
    for batch in _batches(company_side):
        result.appointments.add(upsert(
            conn, appointments, batch,  # type: ignore[arg-type]
            constraint=APPOINTMENT_EDGE, tracked=COMPANY_SIDE_APPOINTMENT_TRACKED,
        ))  # fmt: skip
    for batch in _batches(network_side):
        result.appointments.add(upsert(
            conn, appointments, batch,  # type: ignore[arg-type]
            constraint=APPOINTMENT_EDGE, tracked=NETWORK_APPOINTMENT_TRACKED,
        ))  # fmt: skip

    result.pscs = upsert(
        conn,
        PscRow.__table__,  # type: ignore[arg-type]
        _dedupe(
            [
                {
                    "company_number": n,
                    "psc_id": p.psc_id,
                    "kind": p.kind,
                    "name": p.name,
                    "natures_of_control": list(p.natures_of_control),
                    "notified_on": p.notified_on,
                    "ceased_on": p.ceased_on,
                    "nationality": p.nationality,
                    "country_of_residence": p.country_of_residence,
                }
                for p in b.pscs
                if p.psc_id
            ],
            "psc_id",
        ),
        key=["company_number", "psc_id"],
        tracked=PSC_TRACKED,
    )
    upsert(
        conn,
        PscStatementRow.__table__,  # type: ignore[arg-type]
        _dedupe(
            [
                {
                    "company_number": n,
                    "statement": s.statement,
                    "notified_on": s.notified_on,
                    "ceased_on": s.ceased_on,
                }
                for s in b.psc_statements
            ],
            "statement",
            "notified_on",
        ),
        constraint="uq_psc_statements",
        tracked=["ceased_on"],
    )

    filing_rows = _dedupe(
        [
            {
                "company_number": n,
                "transaction_id": f.transaction_id,
                "category": f.category,
                "type": f.type,
                "date": f.date,
                "description": f.description,
                "description_values": dict(f.description_values),
                "pages": f.pages,
                "paper_filed": f.paper_filed,
                "document_id": f.document_id,
                "fetched_at": b.fetched_at,
            }
            for f in b.filings
        ],
        "transaction_id",
    )
    for batch in _batches(filing_rows):
        result.filings.add(upsert(
            conn, Filing.__table__, batch,  # type: ignore[arg-type]
            key=["company_number", "transaction_id"], tracked=FILING_TRACKED,
        ))  # fmt: skip
    result.documents_queued = _queue_documents(
        conn, n, [f for f in b.filings if wants_document(f, today)]
    )

    result.network_officers = len(b.networks)
    result.network_truncated = sum(1 for net in b.networks.values() if net.truncated)
    result.network_errors = len(b.network_errors)
    result.network_skipped = len(b.network_skipped)
    return result


def wants_document(f: FilingHistoryItem, today: dt.date) -> bool:
    if not f.document_id or f.category not in DOCUMENT_CATEGORIES or not f.date:
        return False
    return f.date >= today.replace(year=today.year - DOCUMENT_LOOKBACK_YEARS)


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


# --- Orchestration -------------------------------------------------------------------


async def ingest_company(
    client: CompaniesHouseClient,
    engine: Engine,
    company_number: str,
    *,
    network: bool = True,
    today: dt.date | None = None,
) -> CompanyResult:
    """Fetch (network I/O) then persist in one transaction: no transaction is held open
    across API calls."""
    bundle = await fetch_bundle(client, company_number, network=network, today=today)
    with engine.begin() as conn:
        return persist_bundle(conn, bundle, today=today)


async def ingest_companies(
    client: CompaniesHouseClient,
    engine: Engine,
    company_numbers: Sequence[str],
    *,
    network: bool = False,
    trigger: str = "cli",
) -> tuple[int, list[CompanyResult]]:
    """Ingest many companies, recording the run and its evidence manifest.
    One company failing doesn't stop the rest."""
    with engine.begin() as conn:
        run_id = conn.execute(
            insert(IngestionRun)
            .values(trigger=trigger, params={"companies": len(company_numbers), "network": network})
            .returning(IngestionRun.id)
        ).scalar_one()

    results: list[CompanyResult] = []
    for number in company_numbers:
        try:
            res = await ingest_company(client, engine, number, network=network)
        except (CompaniesHouseError, ValueError) as exc:
            log.warning("company %s failed: %s", number, exc)
            res = CompanyResult(number, error=str(exc))
        results.append(res)
        log.info(
            "%s appointments+%d filings+%d", number, res.appointments.inserted, res.filings.inserted
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
                manifest={r.company_number: r.snapshot_ids for r in results if not r.error},
                error="; ".join(f"{r.company_number}: {r.error}" for r in failed)[:4000] or None,
                finished_at=func.now(),
            )
        )
    return run_id, results


def summarise(results: Sequence[CompanyResult]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "companies_ok": 0,
        "companies_failed": 0,
        "documents_queued": 0,
        "network_officers": 0,
        "network_truncated": 0,
        "network_errors": 0,
        "network_skipped": 0,
        "linked_companies": 0,
        "snapshots": 0,
    }
    for kind in COUNTED:
        totals[kind] = {"inserted": 0, "updated": 0, "unchanged": 0}
    for r in results:
        totals["companies_failed" if r.error else "companies_ok"] += 1
        for k in ("documents_queued", "network_officers", "network_truncated",
                  "network_errors", "network_skipped", "linked_companies"):  # fmt: skip
            totals[k] += getattr(r, k)
        totals["snapshots"] += len(r.snapshot_ids)
        for kind in COUNTED:
            counts: UpsertCounts = getattr(r, kind)
            for k in ("inserted", "updated", "unchanged"):
                totals[kind][k] += getattr(counts, k)
    return totals
