"""The KYB risk report: the product's output contract.

Everything a reviewer sees is here, and everything factual cites evidence by id
(`E1`, `E2`, …) from one `evidence` registry. The deterministic parts (snapshot, flags,
score, checks) are complete on their own; `narrative` is an optional LLM layer that may
only restate and cite what the deterministic parts already contain.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from ukco.evidence import Citation
from ukco.risk.policy import Severity

REPORT_SCHEMA_VERSION = "1"


class EvidenceItem(BaseModel):
    id: str  # E1, E2, …
    label: str  # human description, e.g. "Officer list", "Accounts 2025-04-05: net assets"
    citation: Citation


class OfficerSummary(BaseModel):
    name: str
    role: str
    appointed_on: dt.date | None = None
    resigned_on: dt.date | None = None


class PscSummary(BaseModel):
    name: str | None
    kind: str
    natures_of_control: list[str] = []
    notified_on: dt.date | None = None
    ceased_on: dt.date | None = None


class FinancialFigure(BaseModel):
    metric: str
    period_end: dt.date
    value: str  # decimal as string: exact, no float rounding
    evidence_id: str


class CompanySnapshot(BaseModel):
    company_number: str
    name: str
    status: str | None
    type: str | None
    incorporated: dt.date | None
    age_years: float | None
    sic_codes: list[str]
    registered_office: str | None
    previous_names: list[str] = []
    current_officers: list[OfficerSummary] = []
    recent_resignations: list[OfficerSummary] = []  # within the last 12 months
    pscs: list[PscSummary] = []
    accounts_next_due: dt.date | None = None
    confirmation_statement_next_due: dt.date | None = None
    financials: list[FinancialFigure] = []  # latest two year-ends
    evidence_ids: list[str] = []


class ReportFlag(BaseModel):
    id: str  # F1, F2, …
    flag_type: str
    severity: Severity
    points: int
    title: str
    explanation: str  # deterministic, generated from data
    evidence_ids: list[str] = Field(min_length=1)
    details: dict[str, object] = {}


class CheckResult(BaseModel):
    """Every rule's outcome, so a reviewer sees what was checked, not just what fired."""

    rule: str
    # "noted": something to verify that isn't a risk signal (no points, no review needed).
    status: Literal["flagged", "clear", "not_assessed", "noted"]
    note: str | None = None
    flag_ids: list[str] = []
    evidence_ids: list[str] = []  # for "noted" checks


class FlagExplanation(BaseModel):
    flag_id: str
    text: str
    evidence_ids: list[str]


class Narrative(BaseModel):
    summary: str
    summary_evidence_ids: list[str]
    flag_explanations: list[FlagExplanation]
    next_steps: list[str]


class LlmRun(BaseModel):
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    served_by: str | None = None  # differs from `model` if a refusal fallback answered


class Provenance(BaseModel):
    rules_version: str  # git sha of the code that produced the report
    policy_version: int
    policy_sha256: str
    ingestion_run_id: int | None = None
    snapshot_ids: list[int] = []
    llm: LlmRun | None = None
    narrative_status: Literal["ok", "disabled", "rejected", "failed"] = "disabled"
    narrative_note: str | None = None  # why disabled/rejected/failed
    timings_ms: dict[str, int] = {}  # per pipeline stage
    companies_house_requests: int | None = None  # network calls (cache hits excluded)
    trace_id: str | None = None  # OpenTelemetry trace id (links to Langfuse)


class RiskReport(BaseModel):
    schema_version: str = REPORT_SCHEMA_VERSION
    generated_at: dt.datetime
    as_of: dt.date
    live: bool
    company: CompanySnapshot
    risk_score: int = Field(ge=0, le=100)
    flags: list[ReportFlag]
    checks: list[CheckResult]
    evidence: list[EvidenceItem]
    narrative: Narrative | None = None
    provenance: Provenance
    disclaimer: str = (
        "Automated screening of public Companies House data. Not financial, legal or "
        "compliance advice; a reviewer must confirm every flag."
    )

    def evidence_by_id(self) -> dict[str, EvidenceItem]:
        return {e.id: e for e in self.evidence}
