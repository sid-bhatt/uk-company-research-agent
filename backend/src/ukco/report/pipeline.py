"""Company number in → validated, cited RiskReport out."""

import datetime as dt

import anthropic
from sqlalchemy import Engine, func
from sqlalchemy.dialects.postgresql import insert

from ukco.companies_house import CompaniesHouseClient
from ukco.config import get_settings
from ukco.db.models import IngestionRun
from ukco.ingest.company import fetch_bundle, persist_bundle, summarise
from ukco.report.build import build_report
from ukco.report.narrative import ClaudeNarrativeWriter, NarrativeWriter, add_narrative
from ukco.report.schema import RiskReport
from ukco.report.validate import DbEvidenceStore, ReportInvalid, validate_report
from ukco.risk import assess


def writer_from_settings() -> NarrativeWriter | None:
    settings = get_settings()
    key = settings.anthropic_api_key
    if not key or not key.get_secret_value().strip():
        return None
    client = anthropic.AsyncAnthropic(api_key=key.get_secret_value().strip())
    return ClaudeNarrativeWriter(client, model=settings.llm_model, effort=settings.llm_effort)


async def generate_report(
    client: CompaniesHouseClient,
    engine: Engine,
    company_number: str,
    *,
    as_of: dt.date | None = None,
    network: bool = True,
    writer: NarrativeWriter | None = None,
) -> RiskReport:
    """Fetch → persist evidence → rules → report → validate → (optional) narrative."""
    bundle = await fetch_bundle(client, company_number, network=network)
    with engine.begin() as conn:
        stored = persist_bundle(conn, bundle)
        run_id = conn.execute(
            insert(IngestionRun)
            .values(
                trigger="report",
                params={"company_number": bundle.company_number, "network": network},
                status="succeeded",
                stats=summarise([stored]),
                manifest={bundle.company_number: stored.snapshot_ids},
                finished_at=func.now(),
            )
            .returning(IngestionRun.id)
        ).scalar_one()

    assessment = assess(bundle, stored.evidence, as_of=as_of)
    report = build_report(
        bundle,
        stored.evidence,
        assessment,
        ingestion_run_id=run_id,
        snapshot_ids=stored.snapshot_ids,
    )
    if problems := validate_report(report, DbEvidenceStore(engine)):
        raise ReportInvalid(problems)  # never show a reviewer an unverifiable report
    return await add_narrative(report, writer)
