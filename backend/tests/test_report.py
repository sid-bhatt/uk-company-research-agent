"""Report assembly, citation validation and the LLM narrative guardrails.

No real LLM calls: narrative writers are fakes, and the Claude writer is exercised
against a stub client that records the request it was sent.
"""

import datetime as dt
import re
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine

from tests.fakes import ACTIVE, LIQUIDATION, FakeCompaniesHouse
from tests.ixbrl_builder import micro_accounts
from ukco.report import render_markdown, validate_report
from ukco.report.narrative import (
    FALLBACK_BETA,
    ClaudeNarrativeWriter,
    NarrativeError,
    OpenAICompatibleNarrativeWriter,
    add_narrative,
    unsupported_numbers,
)
from ukco.report.pipeline import generate_report
from ukco.report.schema import FlagExplanation, LlmRun, Narrative, RiskReport
from ukco.report.validate import DbEvidenceStore

pytestmark = pytest.mark.anyio

RUN = LlmRun(
    model="fake", prompt_version="t", input_tokens=10, output_tokens=5, cost_usd=0.001, latency_ms=3
)


def troubled_company() -> FakeCompaniesHouse:
    """The active fixture company with a sharp fall in net assets in its latest accounts."""
    ch = FakeCompaniesHouse()
    newest, older = ch.accounts_document_ids(ACTIVE)[:2]
    ch.documents[newest] = micro_accounts(
        "2025-04-05",
        "2024-04-05",
        now={"NetAssetsLiabilities": 8_000, "CurrentAssets": 9_000},
        before={"NetAssetsLiabilities": 40_000, "CurrentAssets": 50_000},
    )
    ch.documents[older] = None
    return ch


async def make_report(
    engine: Engine, ch: FakeCompaniesHouse | None = None, **kw: Any
) -> RiskReport:
    ch = ch or troubled_company()
    async with ch.client() as client:
        return await generate_report(client, engine, ACTIVE, **kw)


class FakeWriter:
    def __init__(self, build) -> None:
        self.build = build
        self.calls = 0

    async def write(self, report: RiskReport) -> tuple[Narrative, LlmRun]:
        self.calls += 1
        return self.build(report), RUN


def faithful(report: RiskReport) -> Narrative:
    return Narrative(
        summary=f"The company has {len(report.flags)} flag(s); risk score {report.risk_score}.",
        summary_evidence_ids=[report.flags[0].evidence_ids[0]],
        flag_explanations=[
            FlagExplanation(
                flag_id=f.id, text=f"{f.title}: {f.explanation}", evidence_ids=f.evidence_ids[:1]
            )
            for f in report.flags
        ],
        next_steps=["Ask the customer for current management accounts."],
    )


# --- Deterministic report ---------------------------------------------------------------


@pytest.mark.integration
async def test_report_is_complete_valid_and_cited(engine: Engine) -> None:
    report = await make_report(engine)

    assert report.risk_score == 15 and [f.flag_type for f in report.flags] == [
        "financial_deterioration"
    ]
    assert {c.rule for c in report.checks} >= {
        "overdue_accounts",
        "officer_network",
        "financial_deterioration",
    }
    assert [e.id for e in report.evidence] == [f"E{i}" for i in range(1, len(report.evidence) + 1)]
    assert report.company.financials and all(
        f.evidence_id.startswith("E") for f in report.company.financials
    )
    assert report.provenance.ingestion_run_id and report.provenance.snapshot_ids
    assert report.provenance.narrative_status == "disabled"
    assert validate_report(report, DbEvidenceStore(engine)) == []
    # Labels say what the record is, never a raw JSON pointer.
    assert not [e.label for e in report.evidence if "/items/" in e.label]

    md = render_markdown(report)
    assert "Financial deterioration" in md and "Risk score 15/100" in md
    assert "find-and-update.company-information.service.gov.uk" in md


@pytest.mark.integration
async def test_liquidated_company_report_is_a_valid_knockout(engine: Engine) -> None:
    """Live smoke test, v1: a company in liquidation came out at score 0. Now it's a knockout
    the validator accepts (score 100 rather than the sum of flag points)."""
    async with FakeCompaniesHouse().client() as client:
        report = await generate_report(client, engine, LIQUIDATION)
    assert report.risk_score == 100
    assert report.flags[0].flag_type == "company_status" and report.flags[0].details["knockout"]
    assert validate_report(report, DbEvidenceStore(engine)) == []
    assert "Risk score 100/100" in render_markdown(report)


@pytest.mark.integration
async def test_validator_catches_tampered_or_dangling_citations(engine: Engine) -> None:
    report = await make_report(engine)
    store = DbEvidenceStore(engine)

    api_item = next(e for e in report.evidence if e.citation.kind == "api")
    doc_item = next(e for e in report.evidence if e.citation.kind == "document")
    broken = report.model_copy(deep=True)
    for e in broken.evidence:
        if e.id == api_item.id:
            e.citation.pointer = "/items/999"
        if e.id == doc_item.id:
            e.citation.pointer = "ix:nonFraction[name=core:Invented][contextRef=c9][1]"
    broken.flags[0].evidence_ids.append("E999")
    broken.risk_score = 90

    problems = " | ".join(validate_report(broken, store))
    assert "/items/999" in problems and "core:Invented" in problems
    assert "unknown evidence id E999" in problems and "risk score 90" in problems


@pytest.mark.integration
async def test_back_test_report(engine: Engine) -> None:
    report = await make_report(engine, as_of=dt.date(2025, 9, 1))
    assert not report.live
    assert "overdue_confirmation_statement" in {f.flag_type for f in report.flags}
    assert report.company.accounts_next_due is None  # live-only register field


# --- Narrative guardrails ----------------------------------------------------------------


@pytest.mark.integration
async def test_faithful_narrative_is_attached(engine: Engine) -> None:
    writer = FakeWriter(faithful)
    report = await make_report(engine, writer=writer)

    assert writer.calls == 1
    assert report.provenance.narrative_status == "ok" and report.provenance.llm == RUN
    assert report.narrative and len(report.narrative.flag_explanations) == len(report.flags)
    assert validate_report(report, DbEvidenceStore(engine)) == []
    assert "Why it matters" in render_markdown(report)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda n, r: n.flag_explanations[0].evidence_ids.append("E1"), "outside that flag"),
        (lambda n, r: n.flag_explanations.clear(), "don't match flags"),
        (
            lambda n, r: setattr(n, "summary", "Net assets fell by £31,999 (79.9%)."),
            "numbers not in the source",
        ),
        (lambda n, r: n.summary_evidence_ids.append("E404"), "unknown evidence id E404"),
    ],
    ids=["cites-other-evidence", "missing-flag", "invented-number", "unknown-evidence"],
)
async def test_unfaithful_narratives_are_rejected(engine: Engine, mutate, expected: str) -> None:
    def unfaithful(report: RiskReport) -> Narrative:
        # E1 is always the company profile: never evidence for the financial flag.
        assert "E1" not in report.flags[0].evidence_ids
        n = faithful(report)
        mutate(n, report)
        return n

    report = await make_report(engine, writer=FakeWriter(unfaithful))
    assert report.narrative is None  # fail closed: no prose rather than wrong prose
    assert report.provenance.narrative_status == "rejected"
    assert expected in (report.provenance.narrative_note or "")
    assert report.provenance.llm == RUN  # the spend is still accounted for


async def test_writer_failure_is_recorded_not_raised() -> None:
    class Broken:
        async def write(self, report: RiskReport) -> tuple[Narrative, LlmRun]:
            raise NarrativeError("model declined (category: None)")

    stub = RiskReport.model_construct(provenance=_provenance(), flags=[], narrative=None)
    result = await add_narrative(stub, Broken())
    assert result.provenance.narrative_status == "failed"
    assert "declined" in (result.provenance.narrative_note or "")


def test_unsupported_numbers() -> None:
    source = 'net assets £40,000 → £8,000 (80% down) at 2025-04-05; "risk_score": 15'
    assert unsupported_numbers("Net assets fell 80% to £8,000 by 5 April 2025.", source) == []
    assert unsupported_numbers("Filed on 17 April 2025.", source) == ["17"]
    assert unsupported_numbers("Net assets were £8,000.00.", source) == []
    assert unsupported_numbers("A fall of £32,000.", source) == ["32000"]
    assert unsupported_numbers("See flag F1 and evidence E12.", source) == []  # ids aren't figures


# --- Claude writer (stub client, no network) ---------------------------------------------


class StubMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.request: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        return self.response


def _response(stop_reason: str, text: str | None = None, category: str | None = None) -> Any:
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if stop_reason == "refusal" else None,
        content=content,
        usage=SimpleNamespace(input_tokens=2_000, output_tokens=500),
        model="claude-opus-5-5",
    )


def _stub_client(response: Any) -> tuple[Any, StubMessages]:
    messages = StubMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def _provenance():
    from ukco.report.schema import Provenance

    return Provenance(rules_version="t", policy_version=1, policy_sha256="x")


async def test_claude_writer_request_shape_and_cost() -> None:
    body = Narrative(
        summary="s", summary_evidence_ids=[], flag_explanations=[], next_steps=[]
    ).model_dump_json()
    client, messages = _stub_client(_response("end_turn", body))
    writer = ClaudeNarrativeWriter(client, model="claude-opus-5-5", effort="medium")
    report = RiskReport.model_construct(
        as_of=dt.date(2026, 9, 1),
        live=True,
        risk_score=0,
        flags=[],
        checks=[],
        evidence=[],
        company=SimpleNamespace(model_dump=lambda mode: {}),
    )

    narrative, run = await writer.write(report)

    req = messages.request
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default" and req["betas"] == [FALLBACK_BETA]
    assert req["output_config"]["effort"] == "medium"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in req  # Opus 5.5: thinking can't be disabled; effort is the control
    assert narrative.summary == "s"
    assert run.cost_usd == pytest.approx((2_000 * 4 + 500 * 20) / 1_000_000)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response("refusal", category="cyber"), "declined (category: cyber)"),
        (_response("max_tokens", "{"), "truncated"),
        (_response("end_turn", '{"summary": 1}'), "didn't match the schema"),
    ],
    ids=["refusal", "truncated", "bad-json"],
)
async def test_claude_writer_raises_on_unusable_responses(response: Any, message: str) -> None:
    client, _ = _stub_client(response)
    report = RiskReport.model_construct(
        as_of=dt.date(2026, 9, 1),
        live=True,
        risk_score=0,
        flags=[],
        checks=[],
        evidence=[],
        company=SimpleNamespace(model_dump=lambda mode: {}),
    )
    with pytest.raises(NarrativeError, match=re.escape(message)):
        await ClaudeNarrativeWriter(client).write(report)


# --- OpenAI-compatible writer (stub client, no network) ------------------------------------


def _oai_response(finish: str, text: str | None) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish,
                message=SimpleNamespace(content=text, refusal=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=2_000, completion_tokens=500),
        model="gemini-3.5-flash",
    )


def _oai_client(response: Any) -> tuple[Any, StubMessages]:
    completions = StubMessages(response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        base_url=SimpleNamespace(host="generativelanguage.googleapis.com"),
    )
    return client, completions


def _empty_report() -> RiskReport:
    return RiskReport.model_construct(
        as_of=dt.date(2026, 9, 1),
        live=True,
        risk_score=0,
        flags=[],
        checks=[],
        evidence=[],
        company=SimpleNamespace(model_dump=lambda mode: {}),
    )


async def test_openai_compatible_writer_request_shape() -> None:
    body = Narrative(
        summary="s", summary_evidence_ids=[], flag_explanations=[], next_steps=[]
    ).model_dump_json()
    client, completions = _oai_client(_oai_response("stop", f"```json\n{body}\n```"))
    narrative, run = await OpenAICompatibleNarrativeWriter(client).write(_empty_report())

    req = completions.request
    assert req["model"] == "gemini-3.5-flash"
    assert req["messages"][0]["role"] == "system" and req["messages"][1]["role"] == "user"
    assert req["response_format"]["json_schema"]["schema"]["required"] == [
        "summary",
        "summary_evidence_ids",
        "flag_explanations",
        "next_steps",
    ]
    assert narrative.summary == "s"  # a ```json fence is tolerated
    assert run.cost_usd == 0 and run.input_tokens == 2_000 and run.served_by == "gemini-3.5-flash"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_oai_response("content_filter", None), "declined"),
        (_oai_response("length", "{"), "truncated"),
        (_oai_response("stop", '{"summary": 1}'), "didn't match the schema"),
    ],
    ids=["filtered", "truncated", "bad-json"],
)
async def test_openai_compatible_writer_raises_on_unusable_responses(
    response: Any, message: str
) -> None:
    client, _ = _oai_client(response)
    with pytest.raises(NarrativeError, match=re.escape(message)):
        await OpenAICompatibleNarrativeWriter(client).write(_empty_report())


async def test_noted_check_cites_validated_evidence(engine: Engine) -> None:
    ch = FakeCompaniesHouse()
    newest, older = ch.accounts_document_ids(ACTIVE)[:2]
    ch.documents[newest] = micro_accounts(
        "2025-04-05",
        "2024-04-05",
        now={"NetAssetsLiabilities": 100},
        before={"NetAssetsLiabilities": 100},
    )
    ch.documents[older] = None
    report = await make_report(engine, ch)  # raises if validation fails
    check = next(c for c in report.checks if c.rule == "financial_substance")
    assert check.status == "noted" and len(check.evidence_ids) == 2
    labels = {e.id: e.label for e in report.evidence}
    assert all("net assets" in labels[i].lower() for i in check.evidence_ids)
    assert not any(f.flag_type == "financial_substance" for f in report.flags)

    broken = report.model_copy(
        update={
            "checks": [
                c.model_copy(update={"evidence_ids": []}) if c is check else c
                for c in report.checks
            ]
        }
    )
    assert "a noted check must cite evidence" in " ".join(
        validate_report(broken, DbEvidenceStore(engine))
    )
