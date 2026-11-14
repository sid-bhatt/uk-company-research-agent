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


class LateBand(_Strict):
    min_late: int
    severity: Severity


class CompanyStatusRule(_Strict):
    knockout_statuses: list[str]
    knockout_filing_prefixes: list[str]
    strike_off_filing_prefixes: list[str]
    strike_off_withdrawal_prefixes: list[str]
    strike_off_window_months: int
    strike_off_severity: Severity


class OverdueRule(_Strict):
    severity_by_days_overdue: list[DaysBand]


class FilingHistoryRule(_Strict):
    window_months: int
    grace_days: int
    severity_by_late_filings: list[LateBand]


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


class FinancialRule(_Strict):
    drop_metrics: list[str]
    min_drop_share: float
    min_prior_value: float
    drop_severity: Severity
    net_liabilities_severity: Severity
    turned_negative_severity: Severity


class FinancialSubstanceRule(_Strict):
    max_net_assets: float
    note_dormant: bool


class Rules(_Strict):
    company_status: CompanyStatusRule
    overdue_accounts: OverdueRule
    overdue_confirmation_statement: OverdueRule
    filing_history: FilingHistoryRule
    director_churn: ChangesRule
    psc_changes: ChangesRule
    office_changes: OfficeChangesRule
    new_company_high_risk_sic: NewCompanyRule
    officer_network: OfficerNetworkRule
    financial_deterioration: FinancialRule
    financial_substance: FinancialSubstanceRule


class Policy(_Strict):
    version: int
    points: dict[Severity, int]
    # Per flag type: points = severity points x weight, rounded. Unlisted types weigh 1.
    score_weights: dict[str, float] = {}
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


def band_for_late(bands: list[LateBand], late: int) -> Severity | None:
    return worst([b.severity for b in bands if late >= b.min_late])


def band_for_changes(bands: list[ChangesBand], changes: int) -> Severity | None:
    return worst([b.severity for b in bands if changes >= b.min_changes])
