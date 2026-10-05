"""Request/response models for the HTTP API."""

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ukco.companies_house import normalise_company_number
from ukco.report.schema import RiskReport
from ukco.report.store import ReviewRecord


class CreateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_number: str
    as_of: dt.date | None = Field(None, description="Back-test date; omit for a live report.")
    network: bool = Field(True, description="Check directors' other companies.")
    narrative: bool = Field(True, description="Add the LLM summary if a key is configured.")

    @field_validator("company_number")
    @classmethod
    def _valid_number(cls, value: str) -> str:
        return normalise_company_number(value)  # ValueError → 422

    @field_validator("as_of")
    @classmethod
    def _not_future(cls, value: dt.date | None) -> dt.date | None:
        if value and value > dt.date.today():
            raise ValueError("as_of can't be in the future")
        return value


class CompanyHit(BaseModel):
    company_number: str
    name: str
    status: str | None
    incorporated: dt.date | None
    address: str | None


class ReportCreated(BaseModel):
    id: int
    status: str
    links: dict[str, str]


class ReportSummary(BaseModel):
    id: int
    company_number: str
    as_of: dt.date | None
    status: str
    requested_by: str
    risk_score: int | None
    cost_usd: Decimal | None
    error: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag_id: str | None = Field(
        None, description="F1, F2, … to review a flag; omit for the report."
    )
    decision: Literal["confirm", "dismiss", "approve", "reject"]
    note: str | None = Field(None, max_length=2000)


class ReviewOut(BaseModel):
    id: int
    target: str
    decision: str
    note: str | None
    reviewer: str
    created_at: dt.datetime
    rules_version: str | None
    model_id: str | None

    @classmethod
    def from_record(cls, r: ReviewRecord) -> "ReviewOut":
        return cls(
            id=r.id,
            target=r.target,
            decision=r.decision,
            note=r.note,
            reviewer=r.reviewer,
            created_at=r.created_at,
            rules_version=r.rules_version,
            model_id=r.model_id,
        )


class ReportDetail(ReportSummary):
    report: RiskReport | None
    decisions: dict[str, ReviewOut]  # latest decision per target: "report", "F1", …
