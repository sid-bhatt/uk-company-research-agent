"""Metadata ingestion against a real Postgres, with Companies House mocked from fixtures."""

import copy
import datetime as dt
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select, update

from tests.conftest import alembic_config, load_fixture
from ukco.companies_house import CompaniesHouseClient, RateLimiter, RetryPolicy
from ukco.db.models import Base, Company, Document, Filing, IngestionRun, Officer
from ukco.ingest.metadata import ingest_companies, ingest_company

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

GREGGS = "00502851"
TODAY = dt.date(2026, 9, 28)  # fixes which filings fall inside the 3-year document window


class FakeCompaniesHouse:
    """Serves recorded Greggs fixtures; tests mutate `officers` / `filings` between runs."""

    def __init__(self) -> None:
        self.company = load_fixture("company_greggs")
        self.officers = load_fixture("officers_greggs")
        self.filings = load_fixture("filing_history_greggs_accounts")

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        start = int(request.url.params.get("start_index", 0))
        if path == f"/company/{GREGGS}":
            return httpx.Response(200, json=self.company)
        if path == f"/company/{GREGGS}/officers":
            return httpx.Response(200, json=self._page(self.officers, start))
        if path == f"/company/{GREGGS}/filing-history":
            return httpx.Response(200, json=self._page(self.filings, start))
        return httpx.Response(404, json={"errors": [{"error": "company-profile-not-found"}]})

    @staticmethod
    def _page(data: dict[str, Any], start: int) -> dict[str, Any]:
        # Fixtures hold one page; later pages come back empty, ending pagination.
        return data if start == 0 else {**data, "items": []}

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


def count(engine: Engine, model: type) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(model)).scalar_one()


async def test_first_run_inserts_everything(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        result = await ingest_company(client, engine, GREGGS, today=TODAY)

    n_officers = len(ch.officers["items"])
    n_filings = len(ch.filings["items"])
    assert (result.companies.inserted, result.officers.inserted) == (1, n_officers)
    assert result.filings.inserted == n_filings
    assert result.documents_queued == 3  # accounts filed on/after 2023-09-28
    assert count(engine, Company) == 1
    assert count(engine, Officer) == n_officers
    assert count(engine, Filing) == n_filings

    with engine.connect() as conn:
        raw = conn.execute(select(Company.raw)).scalar_one()
    assert raw["company_name"] == "GREGGS PLC"  # the API response itself is stored


async def test_rerun_is_a_no_op(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        await ingest_company(client, engine, GREGGS, today=TODAY)
        with engine.connect() as conn:
            before = conn.execute(select(func.max(Filing.updated_at))).scalar_one()
        second = await ingest_company(client, engine, GREGGS, today=TODAY)

    assert second.companies.unchanged == 1
    assert second.officers.inserted == second.officers.updated == 0
    assert second.filings.inserted == second.filings.updated == 0
    assert second.documents_queued == 0
    with engine.connect() as conn:
        after = conn.execute(select(func.max(Filing.updated_at))).scalar_one()
    assert after == before  # untouched rows keep their timestamps


async def test_only_changed_rows_are_updated(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        await ingest_company(client, engine, GREGGS, today=TODAY)

        ch.officers = copy.deepcopy(ch.officers)
        ch.officers["items"][0]["resigned_on"] = "2026-09-01"
        result = await ingest_company(client, engine, GREGGS, today=TODAY)

    assert result.officers.updated == 1
    assert result.officers.unchanged == len(ch.officers["items"]) - 1
    assert result.filings.updated == 0


async def test_requeue_never_resets_document_progress(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        await ingest_company(client, engine, GREGGS, today=TODAY)
        with engine.begin() as conn:
            conn.execute(update(Document).values(status="parsed"))
        await ingest_company(client, engine, GREGGS, today=TODAY)

    with engine.connect() as conn:
        statuses = set(conn.execute(select(Document.status)).scalars())
    assert statuses == {"parsed"}


async def test_run_is_recorded_and_one_failure_does_not_stop_others(engine: Engine) -> None:
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
    assert run["stats"]["companies_ok"] == 1
    assert run["stats"]["companies_failed"] == 1
    assert run["finished_at"] is not None
    assert "99999999" in (run["error"] or "")


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
