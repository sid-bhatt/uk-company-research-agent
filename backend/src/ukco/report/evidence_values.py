"""Read a cited value back from stored evidence, so assistants can quote the record itself
(shared by the MCP tools and the report chat)."""

import json
from typing import Any

from sqlalchemy import Engine, select

from ukco.db.models import FinancialFactRow
from ukco.evidence import resolve_pointer
from ukco.report.schema import EvidenceItem
from ukco.report.validate import DbEvidenceStore

MAX_VALUE_CHARS = 1500  # evidence values are JSON excerpts; keep tool results compact


def evidence_value(
    engine: Engine, item: EvidenceItem, store: DbEvidenceStore | None = None
) -> dict[str, Any]:
    c = item.citation
    out: dict[str, Any] = {"id": item.id, "label": item.label, "url": c.url}
    if c.kind == "api":
        body = (store or DbEvidenceStore(engine)).snapshot_body(c.snapshot_id or 0)
        try:
            value = resolve_pointer(body, c.pointer) if body is not None else None
        except (KeyError, IndexError, TypeError):
            value = None
        text = json.dumps(value, default=str)
        out["value"] = text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "…"
        return out
    with engine.connect() as conn:
        fact = conn.execute(
            select(
                FinancialFactRow.metric, FinancialFactRow.period_end, FinancialFactRow.value
            ).where(
                FinancialFactRow.document_id == c.document_id, FinancialFactRow.locator == c.pointer
            )
        ).first()
    out["value"] = (
        {"metric": fact.metric, "period_end": str(fact.period_end), "value": str(fact.value)}
        if fact
        else None
    )
    return out
