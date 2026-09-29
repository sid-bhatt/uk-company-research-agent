"""Company ingestion against a real Postgres, with Companies House served from fixtures.

Greggs fixtures exercise idempotency at scale; the pseudonymised March-2021 small-company
fixtures exercise the KYB bundle: PSCs, the officer network, stubs and evidence.
"""

import copy
import datetime as dt
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select, update

from tests.conftest import FIXTURES, alembic_config, load_fixture
from ukco.companies_house import CompaniesHouseClient, RateLimiter, RetryPolicy
from ukco.companies_house.models import Officer as ApiOfficer
from ukco.db.models import (
    Appointment,
    Base,
    Company,
    Document,
    Filing,
    IngestionRun,
    Officer,
    Psc,
    SourceSnapshot,
)
from ukco.evidence import resolve_pointer
from ukco.ingest.company import ingest_companies, ingest_company, network_candidates

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

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


def count(engine: Engine, model: type, *where: Any) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


# --- Idempotency (Greggs, no network) --------------------------------------------------


async def test_first_run_inserts_everything(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        result = await ingest_company(client, engine, GREGGS, network=False, today=TODAY)

    n_officers = len(ch.routes[f"/company/{GREGGS}/officers"]["items"])
    n_filings = len(ch.routes[f"/company/{GREGGS}/filing-history"]["items"])
    assert result.companies.inserted == 1
    assert result.appointments.inserted == n_officers
    assert result.filings.inserted == n_filings
    assert result.documents_queued == 3  # accounts filed on/after 2023-09-28
    assert count(engine, Appointment) == n_officers
    assert count(engine, Officer) == n_officers  # one person per officer here
    assert count(engine, Filing) == n_filings

    with engine.connect() as conn:
        raw, fetched = conn.execute(select(Company.raw, Company.profile_fetched_at)).one()
    assert raw and raw["company_name"] == "GREGGS PLC"  # the API response itself is stored
    assert fetched is not None


async def test_rerun_is_a_no_op_and_reuses_snapshots(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        first = await ingest_company(client, engine, GREGGS, network=False, today=TODAY)
        with engine.connect() as conn:
            before = conn.execute(select(func.max(Filing.updated_at))).scalar_one()
        second = await ingest_company(client, engine, GREGGS, network=False, today=TODAY)

    assert second.companies.unchanged == 1
    assert second.appointments.inserted == second.appointments.updated == 0
    assert second.filings.inserted == second.filings.updated == 0
    assert second.documents_queued == 0
    assert second.snapshot_ids == first.snapshot_ids  # identical responses share a row
    with engine.connect() as conn:
        after = conn.execute(select(func.max(Filing.updated_at))).scalar_one()
    assert after == before


async def test_only_changed_rows_are_updated(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        await ingest_company(client, engine, GREGGS, network=False, today=TODAY)

        officers = copy.deepcopy(ch.routes[f"/company/{GREGGS}/officers"])
        officers["items"][0]["resigned_on"] = "2026-09-01"
        ch.routes[f"/company/{GREGGS}/officers"] = officers
        result = await ingest_company(client, engine, GREGGS, network=False, today=TODAY)

    assert result.appointments.updated == 1
    assert result.appointments.unchanged == len(officers["items"]) - 1
    assert result.filings.updated == 0


async def test_requeue_never_resets_document_progress(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        await ingest_company(client, engine, GREGGS, network=False, today=TODAY)
        with engine.begin() as conn:
            conn.execute(update(Document).values(status="parsed"))
        await ingest_company(client, engine, GREGGS, network=False, today=TODAY)

    with engine.connect() as conn:
        statuses = set(conn.execute(select(Document.status)).scalars())
    assert statuses == {"parsed"}


async def test_run_records_stats_manifest_and_survives_failures(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        run_id, results = await ingest_companies(client, engine, ["99999999", GREGGS])

    assert [bool(r.error) for r in results] == [True, False]
    with engine.connect() as conn:
        run = (
            conn.execute(select(IngestionRun.__table__).where(IngestionRun.id == run_id))
            .mappings()
            .one()
        )
    assert run["status"] == "partial"
    assert run["stats"]["companies_ok"] == 1 and run["stats"]["companies_failed"] == 1
    assert run["manifest"][GREGGS] == results[1].snapshot_ids
    assert "99999999" in (run["error"] or "")


# --- KYB bundle (small company, with network) -----------------------------------------


async def test_bundle_persists_pscs_people_and_network(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        result = await ingest_company(client, engine, ACTIVE, today=TODAY)

    n_pscs = len(ch.routes[f"/company/{ACTIVE}/persons-with-significant-control"]["items"])
    assert result.pscs.inserted == n_pscs > 0
    assert count(engine, Psc, Psc.company_number == ACTIVE) == n_pscs
    # One officer's network was recorded; any others 404 and are reported, not fatal.
    assert result.network_officers == 1
    with engine.connect() as conn:
        total, fetched = conn.execute(
            select(Officer.total_appointments, Officer.appointments_fetched_at).where(
                Officer.officer_id == ch.networked_officer
            )
        ).one()
    assert total is not None and fetched is not None


async def test_company_and_network_views_of_one_appointment_merge(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        await ingest_company(client, engine, ACTIVE, today=TODAY)

    edge = (Appointment.company_number == ACTIVE) & (Appointment.officer_id == ch.networked_officer)
    assert count(engine, Appointment, edge) == 1
    with engine.connect() as conn:
        appointment_id = conn.execute(select(Appointment.appointment_id).where(edge)).scalar_one()
    assert appointment_id  # the richer company-side row wasn't clobbered by the network row


async def test_stub_data_never_overwrites_a_full_profile(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    network = copy.deepcopy(ch.routes[f"/officers/{ch.networked_officer}/appointments"])
    network["items"].append(
        {
            "appointed_to": {
                "company_number": LIQUIDATION,
                "company_name": "WRONG NAME FROM NETWORK",
                "company_status": "active",
            },
            "officer_role": "director",
            "appointed_on": "2021-03-01",
        }
    )
    network["total_results"] = len(network["items"])
    ch.routes[f"/officers/{ch.networked_officer}/appointments"] = network

    async with ch.client() as client:
        await ingest_company(client, engine, LIQUIDATION, network=False, today=TODAY)
        result = await ingest_company(client, engine, ACTIVE, today=TODAY)

    assert result.linked_companies >= 1
    with engine.connect() as conn:
        status, name = conn.execute(
            select(Company.status, Company.name).where(Company.company_number == LIQUIDATION)
        ).one()
    assert status == "liquidation" and name != "WRONG NAME FROM NETWORK"


async def test_every_citation_resolves_to_the_fact_it_names(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        result = await ingest_company(client, engine, ACTIVE, today=TODAY)

    with engine.connect() as conn:
        bodies = dict(
            conn.execute(
                select(SourceSnapshot.id, SourceSnapshot.body).where(
                    SourceSnapshot.id.in_(result.snapshot_ids)
                )
            ).all()
        )

    ev = result.evidence
    profile = ev.require("profile", ACTIVE)
    assert resolve_pointer(bodies[profile.snapshot_id], profile.pointer)["company_number"] == ACTIVE

    for item in ch.routes[f"/company/{ACTIVE}/filing-history"]["items"]:
        c = ev.require("filing", ACTIVE, item["transaction_id"])
        assert (
            resolve_pointer(bodies[c.snapshot_id], c.pointer)["transaction_id"]
            == item["transaction_id"]
        )

    for item in ch.routes[f"/company/{ACTIVE}/persons-with-significant-control"]["items"]:
        psc_id = item["links"]["self"].rsplit("/", 1)[-1]
        c = ev.require("psc", ACTIVE, psc_id)
        assert resolve_pointer(bodies[c.snapshot_id], c.pointer)["links"]["self"].endswith(psc_id)
        assert c.url.startswith("https://find-and-update.company-information.service.gov.uk/")

    # Absences are citable too: "no insolvency on file" points at the stored empty response.
    assert ev.require("register", f"/company/{ACTIVE}/insolvency").pointer == ""


def test_network_candidates_skip_secretaries_corporates_and_old_resignations() -> None:
    def officer(oid: str, role: str, resigned: str | None = None) -> ApiOfficer:
        return ApiOfficer.model_validate(
            {
                "name": oid,
                "officer_role": role,
                "resigned_on": resigned,
                "links": {"officer": {"appointments": f"/officers/{oid}/appointments"}},
            }
        )

    picked = network_candidates(
        [
            officer("OldDirector0001", "director", "2020-01-01"),  # outside 3-year window
            officer("RecentDirector01", "director", "2025-06-01"),
            officer("Secretary000001", "secretary"),
            officer("CorporateDir001", "corporate-director"),
            officer("ActiveDirector01", "director"),
            officer("ActiveDirector01", "director"),  # listed twice: one check
        ],
        TODAY,
    )
    assert picked == ["ActiveDirector01", "RecentDirector01"]


def test_migrations_match_models_and_roundtrip(database_url: str, engine: Engine) -> None:
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations disagree: {diff}"

    engine.dispose()  # release connections so downgrade can drop tables
    cfg = alembic_config(database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
