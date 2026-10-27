"""Tracing with OpenTelemetry, exported over OTLP (to Langfuse when its keys are set).

Vendor-neutral on purpose: the code only uses the OpenTelemetry API; Langfuse is just the
configured backend (any OTLP collector works via OTEL_EXPORTER_OTLP_ENDPOINT). With no
backend configured, the API's no-op tracer makes every span free.

Each report is one trace: a root span with child spans per pipeline stage, and the LLM
call as a Langfuse "generation" (model, token usage, cost). Prompt and completion *content*
are not exported unless TRACE_CONTENT=true: the input names company officers, and a
tracing vendor shouldn't receive personal data by default.
"""

import base64
import json
import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from ukco import __version__
from ukco.config import get_settings

log = logging.getLogger(__name__)
_provider: TracerProvider | None = None


def tracer() -> trace.Tracer:
    return trace.get_tracer("ukco", __version__)


def _default_exporter() -> SpanExporter | None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    s = get_settings()
    if s.langfuse_public_key and s.langfuse_secret_key:
        token = f"{s.langfuse_public_key}:{s.langfuse_secret_key.get_secret_value()}"
        return OTLPSpanExporter(
            endpoint=f"{s.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": "Basic " + base64.b64encode(token.encode()).decode()},
        )
    if s.otel_exporter_otlp_endpoint:
        return OTLPSpanExporter()  # reads the standard OTEL_EXPORTER_OTLP_* variables
    return None


def setup_tracing(service_name: str, exporter: SpanExporter | None = None) -> bool:
    """Install a tracer provider once per process. Returns whether spans are exported."""
    global _provider
    if _provider is not None:
        return True
    exporter = exporter or _default_exporter()
    if exporter is None:
        return False
    s = get_settings()
    _provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.version": __version__,
                "deployment.environment": s.env,
            }
        )
    )
    _provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)
    log.info("tracing enabled for %s", service_name)
    return True


def shutdown_tracing() -> None:
    """Flush buffered spans (CLI runs exit quickly; don't lose the last trace)."""
    if _provider is not None:
        _provider.force_flush()


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def record_generation(
    span: trace.Span,
    *,
    model: str,
    served_by: str | None,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    prompt: str | None = None,
    completion: str | None = None,
    system: str = "anthropic",
) -> None:
    """Mark a span as an LLM generation in Langfuse's OpenTelemetry mapping."""
    attributes: dict[str, Any] = {
        "langfuse.observation.type": "generation",
        "gen_ai.system": system,
        "gen_ai.request.model": model,
        "gen_ai.response.model": served_by or model,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.usage.cost": cost_usd,
        "langfuse.observation.usage_details": json.dumps(
            {"input": input_tokens, "output": output_tokens}
        ),
        "langfuse.observation.cost_details": json.dumps({"total": cost_usd}),
    }
    if get_settings().trace_content:
        if prompt is not None:
            attributes["langfuse.observation.input"] = prompt
        if completion is not None:
            attributes["langfuse.observation.output"] = completion
    span.set_attributes(attributes)
