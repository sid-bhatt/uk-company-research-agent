"""Tracing: one trace per report, a span per stage, the LLM call as a generation."""

import datetime as dt
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import Engine

from tests.fakes import ACTIVE
from tests.test_report import _response, _stub_client, troubled_company
from ukco.config import get_settings
from ukco.report.narrative import ClaudeNarrativeWriter
from ukco.report.pipeline import generate_report
from ukco.report.schema import Narrative

pytestmark = pytest.mark.anyio

_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _provider() -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(provider)


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    _EXPORTER.clear()
    yield _EXPORTER


def ids(span: ReadableSpan) -> tuple[int, int]:
    """(trace id, span id); a finished span always has a context."""
    assert span.context is not None
    return span.context.trace_id, span.context.span_id


def by_name(exporter: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    return {s.name: s for s in exporter.get_finished_spans()}


@pytest.mark.integration
async def test_one_trace_per_report_with_a_span_per_stage(
    engine: Engine, spans: InMemorySpanExporter
) -> None:
    async with troubled_company().client() as client:
        report = await generate_report(client, engine, ACTIVE, requested_by="analyst@example.com")

    named = by_name(spans)
    root = named["kyb.report"]
    assert {"fetching", "storing_evidence", "assessing", "validating"} <= set(named)
    assert all(ids(s)[0] == ids(root)[0] for s in named.values())
    assert all(
        (parent := named[s].parent) is not None and parent.span_id == ids(root)[1]
        for s in ("fetching", "assessing", "validating")
    )
    assert root.attributes is not None
    assert (
        root.attributes["kyb.risk_score"] == 15
        and root.attributes["langfuse.user.id"] == "analyst@example.com"
    )

    p = report.provenance
    assert p.trace_id == format(ids(root)[0], "032x")
    assert set(p.timings_ms) >= {"fetching", "storing_evidence", "assessing", "validating"}
    assert p.companies_house_requests and p.companies_house_requests > 0


@pytest.mark.integration
async def test_failed_report_marks_the_trace_as_an_error(
    engine: Engine, spans: InMemorySpanExporter
) -> None:
    async with troubled_company().client() as client:
        with pytest.raises(Exception, match="Not found"):
            await generate_report(client, engine, "99999999")
    root = by_name(spans)["kyb.report"]
    assert root.status.status_code == StatusCode.ERROR


async def _generate(spans: InMemorySpanExporter) -> ReadableSpan:
    from ukco.report.schema import RiskReport

    body = Narrative(
        summary="s", summary_evidence_ids=[], flag_explanations=[], next_steps=[]
    ).model_dump_json()
    client, _ = _stub_client(_response("end_turn", body))
    report = RiskReport.model_construct(
        as_of=dt.date(2026, 9, 1),
        live=True,
        risk_score=0,
        flags=[],
        checks=[],
        evidence=[],
        company=type("C", (), {"model_dump": lambda self, mode: {"name": "PERSON 1"}})(),
    )
    await ClaudeNarrativeWriter(client).write(report)
    return by_name(spans)["narrative.generate"]


async def test_llm_call_is_a_langfuse_generation_without_content_by_default(
    spans: InMemorySpanExporter,
) -> None:
    span = await _generate(spans)
    attrs = span.attributes or {}
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["gen_ai.request.model"] == "claude-opus-5-5"
    assert (attrs["gen_ai.usage.input_tokens"], attrs["gen_ai.usage.output_tokens"]) == (2000, 500)
    assert attrs["gen_ai.usage.cost"] == pytest.approx(0.018)
    assert "langfuse.observation.input" not in attrs  # prompts name officers: opt-in only


async def test_content_is_exported_only_when_opted_in(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "trace_content", True)
    attrs = (await _generate(spans)).attributes or {}
    assert "PERSON 1" in str(attrs["langfuse.observation.input"])
    assert '"summary":"s"' in str(attrs["langfuse.observation.output"]).replace(" ", "")
