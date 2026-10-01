"""Company number in → validated, cited RiskReport out."""

import datetime as dt
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import anthropic
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import Engine, func
from sqlalchemy.dialects.postgresql import insert

from ukco.companies_house import CompaniesHouseClient
from ukco.config import get_settings
from ukco.db.models import IngestionRun
from ukco.ingest.company import fetch_bundle, persist_bundle, summarise
from ukco.observability import current_trace_id, tracer
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
    on_stage: Callable[[str], None] | None = None,
    requested_by: str | None = None,
) -> RiskReport:
    """Fetch → persist evidence → rules → report → validate → (optional) narrative.

    One trace per report, a child span and a timing per stage. `on_stage` is told each
    stage as it starts, for progress streaming."""
    timings: dict[str, int] = {}
    requests_before = client.requests

    @contextmanager
    def stage(name: str) -> Iterator[trace.Span]:
        if on_stage:
            on_stage(name)
        started = time.perf_counter()
        with tracer().start_as_current_span(name) as span:
            yield span
        timings[name] = int((time.perf_counter() - started) * 1000)

    with tracer().start_as_current_span("kyb.report") as root:
        root.set_attributes(
            {
                "langfuse.trace.name": "kyb-report",
                "kyb.company_number": company_number,
                "kyb.as_of": str(as_of or "live"),
                "kyb.network": network,
            }
        )
        if requested_by:
            root.set_attribute("langfuse.user.id", requested_by)
        try:
            with stage("fetching") as span:
                bundle = await fetch_bundle(
                    client, company_number, network=network, accounts_as_of=as_of
                )
                span.set_attribute("kyb.api_responses", len(bundle.responses))
            with stage("storing_evidence"), engine.begin() as conn:
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
            with stage("assessing"):
                assessment = assess(bundle, stored.evidence, as_of=as_of)
                report = build_report(
                    bundle,
                    stored.evidence,
                    assessment,
                    ingestion_run_id=run_id,
                    snapshot_ids=stored.snapshot_ids,
                )
            with stage("validating") as span:
                problems = validate_report(report, DbEvidenceStore(engine))
                span.set_attribute("kyb.citations", len(report.evidence))
                if problems:
                    raise ReportInvalid(problems)  # never show a reviewer an unverifiable report
            if writer:
                with stage("writing_narrative"):
                    report = await add_narrative(report, writer)
            else:
                report = await add_narrative(report, None)
        except Exception as exc:
            root.record_exception(exc)
            root.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise

        provenance = report.provenance.model_copy(
            update={
                "timings_ms": timings,
                "companies_house_requests": client.requests - requests_before,
                "trace_id": current_trace_id(),
            }
        )
        root.set_attributes(
            {
                "kyb.risk_score": report.risk_score,
                "kyb.flags": len(report.flags),
                "kyb.narrative_status": provenance.narrative_status,
                "kyb.companies_house_requests": provenance.companies_house_requests,
            }
        )
        return report.model_copy(update={"provenance": provenance})
