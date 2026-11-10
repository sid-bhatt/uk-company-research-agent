"""What the rules engine produces."""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from ukco.evidence import Citation
from ukco.risk.policy import Severity


class Flag(BaseModel):
    flag_type: str
    severity: Severity
    points: int
    title: str
    explanation: str  # one factual sentence, generated from data (not an LLM)
    evidence: list[Citation] = Field(min_length=1)  # a flag without evidence is invalid
    details: dict[str, Any] = {}


class RuleOutcome(BaseModel):
    rule: str
    # "noted": context for the reviewer to verify, not a risk signal (no flag, no points).
    status: Literal["flagged", "clear", "not_assessed", "noted"]
    flags: list[Flag] = []
    note: str | None = None  # why clear / why not assessed / what to verify / caveats
    evidence: list[Citation] = []  # what a "noted" outcome rests on


class ScoreContribution(BaseModel):
    flag_type: str
    severity: Severity
    points: int


class Assessment(BaseModel):
    company_number: str
    company_name: str
    as_of: dt.date
    live: bool  # as_of is the fetch date: live register markers are usable
    policy_version: int
    policy_sha256: str
    outcomes: list[RuleOutcome]
    risk_score: int
    contributions: list[ScoreContribution]

    @property
    def flags(self) -> list[Flag]:
        return [f for o in self.outcomes for f in o.flags]
