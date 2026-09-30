"""LLM narrative: an executive summary and a plain-English explanation per flag.

The LLM never sees raw register data and never decides anything: it receives the
finished deterministic report (flags, checks, snapshot, evidence labels) and writes
prose that must cite the evidence ids it was given. Output is schema-constrained
(structured outputs) and then checked; a narrative that cites unknown or unrelated
evidence, or states a number that isn't in its input, is rejected (fail closed:
the report ships without a narrative rather than with an unfaithful one).
"""

import json
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types.beta import BetaOutputConfigParam
from pydantic import ValidationError

from ukco.report.schema import LlmRun, Narrative, RiskReport
from ukco.report.validate import narrative_problems

PROMPT_VERSION = "narrative-v1"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# USD per million tokens (input, output). Opus 5.5 list price.
PRICING: dict[str, tuple[float, float]] = {"claude-opus-5-5": (4.0, 20.0)}

SYSTEM_PROMPT = """You write the narrative section of a KYB (know-your-business) risk report \
that a compliance analyst at a UK fintech will review before onboarding a business customer.

You receive the finished report as JSON: company snapshot, red flags produced by \
deterministic rules, the outcome of every check, and an evidence registry (ids E1, E2, …).

Write:
- summary: 2-4 sentences on the overall picture, starting with the most material flags. \
If there are no flags, say which checks were clear and which could not be assessed.
- flag_explanations: exactly one per flag, keyed by its id, explaining in 1-2 sentences \
what the flag means and why it matters for onboarding risk.
- next_steps: 1-4 concrete things the analyst should verify or request from the customer.

Rules:
- Use only facts in the input. Do not add risks, speculate about causes or intent, or \
reach legal conclusions; the analyst decides.
- Every number you write must appear in the input exactly as written there. Do not \
compute new figures (no new percentages, totals or differences).
- Cite evidence by id in the evidence_ids fields. A flag's explanation may cite only \
that flag's own evidence ids.
- Neutral, factual British English. No markdown."""

NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "summary_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "flag_explanations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "flag_id": {"type": "string"},
                    "text": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["flag_id", "text", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "summary_evidence_ids", "flag_explanations", "next_steps"],
    "additionalProperties": False,
}


class NarrativeError(RuntimeError):
    """The model didn't produce a usable narrative (refusal, truncation, bad JSON)."""


class NarrativeWriter(Protocol):
    async def write(self, report: RiskReport) -> tuple[Narrative, LlmRun]: ...


def narrative_input(report: RiskReport) -> str:
    """What the model sees: the deterministic report, without raw citation internals."""
    payload = {
        "as_of": str(report.as_of),
        "mode": "live" if report.live else "back-test",
        "risk_score": report.risk_score,
        "company": report.company.model_dump(mode="json"),
        "flags": [f.model_dump(mode="json") for f in report.flags],
        "checks": [c.model_dump(mode="json") for c in report.checks],
        "evidence": [{"id": e.id, "label": e.label} for e in report.evidence],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


class ClaudeNarrativeWriter:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str = "claude-opus-5-5",
        effort: Effort = "medium",
        max_tokens: int = 16000,
    ) -> None:
        self.client = client
        self.model = model
        self.effort: Effort = effort
        self.max_tokens = max_tokens

    async def write(self, report: RiskReport) -> tuple[Narrative, LlmRun]:
        output_config: BetaOutputConfigParam = {
            "effort": self.effort,
            "format": {"type": "json_schema", "schema": NARRATIVE_SCHEMA},
        }
        started = time.perf_counter()
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": narrative_input(report)}],
            output_config=output_config,
            # A safety decline is re-run on Anthropic's recommended fallback model.
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise NarrativeError(f"model declined (category: {category})")
        if response.stop_reason == "max_tokens":
            raise NarrativeError("narrative truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise NarrativeError("no text in response")
        try:
            narrative = Narrative.model_validate_json(text)
        except ValidationError as exc:
            raise NarrativeError(
                f"narrative didn't match the schema: {exc.error_count()} errors"
            ) from exc

        usage = response.usage
        price_in, price_out = PRICING.get(self.model, (0.0, 0.0))
        cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000
        return narrative, LlmRun(
            model=self.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=round(cost, 6),
            latency_ms=latency_ms,
            served_by=response.model,
        )


_NUMBER = re.compile(r"(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    out = set()
    for raw in _NUMBER.findall(text):
        try:
            value = Decimal(raw.replace(",", "").rstrip("."))
        except InvalidOperation:
            continue
        out.add(format(value.normalize(), "f"))
    return out


def unsupported_numbers(narrative_text: str, source_text: str) -> list[str]:
    """Numbers the narrative states that its input never contained."""
    return sorted(_numbers(narrative_text) - _numbers(source_text))


def _narrative_text(report: RiskReport) -> str:
    n = report.narrative
    assert n is not None
    return " ".join([n.summary, *(e.text for e in n.flag_explanations), *n.next_steps])


async def add_narrative(report: RiskReport, writer: NarrativeWriter | None) -> RiskReport:
    """Attach a validated narrative, or record why there isn't one. Never raises."""
    prov = report.provenance
    if writer is None:
        prov = prov.model_copy(
            update={
                "narrative_status": "disabled",
                "narrative_note": "No ANTHROPIC_API_KEY configured.",
            }
        )
        return report.model_copy(update={"provenance": prov})

    try:
        narrative, run = await writer.write(report)
    except (NarrativeError, anthropic.APIError) as exc:
        prov = prov.model_copy(
            update={"narrative_status": "failed", "narrative_note": str(exc)[:500]}
        )
        return report.model_copy(update={"provenance": prov})

    candidate = report.model_copy(update={"narrative": narrative})
    problems = narrative_problems(candidate)
    if extra := unsupported_numbers(_narrative_text(candidate), narrative_input(report)):
        problems.append(f"numbers not in the source data: {extra}")
    if problems:
        prov = prov.model_copy(
            update={
                "llm": run,
                "narrative_status": "rejected",
                "narrative_note": "; ".join(problems)[:1000],
            }
        )
        return report.model_copy(update={"provenance": prov})

    prov = prov.model_copy(update={"llm": run, "narrative_status": "ok", "narrative_note": None})
    return candidate.model_copy(update={"provenance": prov})
