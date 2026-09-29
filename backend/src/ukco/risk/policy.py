"""The risk policy: every threshold, severity and point value, loaded from YAML."""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

Severity = Literal["low", "medium", "high"]
SEVERITY_ORDER: dict[Severity, int] = {"low": 1, "medium": 2, "high": 3}
DEFAULT_POLICY = Path(__file__).with_name("risk_policy.yaml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # typos in policy fail loudly


class DaysBand(_Strict):
    min_days: int
    severity: Severity


class ChangesBand(_Strict):
    min_changes: int
    severity: Severity


class OverdueRule(_Strict):
    severity_by_days_overdue: list[DaysBand]


class ChangesRule(_Strict):
    window_months: int
    severity_by_changes: list[ChangesBand]
    ignore_days_after_incorporation: int = 0


class OfficeChangesRule(ChangesRule):
    disputed_or_undeliverable_severity: Severity


class NewCompanyRule(_Strict):
    max_age_months: int
    severity: Severity


class OfficerNetworkRule(_Strict):
    min_adverse_links: int
    min_adverse_share: float
    min_insolvency_links: int
    adverse_statuses: list[str]
    insolvency_statuses: list[str]
    severity: Severity
    severity_if_insolvency_links: Severity


class Rules(_Strict):
    overdue_accounts: OverdueRule
    overdue_confirmation_statement: OverdueRule
    director_churn: ChangesRule
    psc_changes: ChangesRule
    office_changes: OfficeChangesRule
    new_company_high_risk_sic: NewCompanyRule
    officer_network: OfficerNetworkRule


class Policy(_Strict):
    version: int
    points: dict[Severity, int]
    rules: Rules
    high_risk_sic_codes: dict[str, str]
    sha256: str = ""  # of the source file; recorded on every report


def load_policy(path: Path = DEFAULT_POLICY) -> Policy:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    return Policy.model_validate({**data, "sha256": hashlib.sha256(raw).hexdigest()})


@lru_cache
def default_policy() -> Policy:
    return load_policy()


def worst(severities: list[Severity]) -> Severity | None:
    return max(severities, key=lambda s: SEVERITY_ORDER[s]) if severities else None


def band_for_days(bands: list[DaysBand], days: int) -> Severity | None:
    return worst([b.severity for b in bands if days >= b.min_days])


def band_for_changes(bands: list[ChangesBand], changes: int) -> Severity | None:
    return worst([b.severity for b in bands if changes >= b.min_changes])
