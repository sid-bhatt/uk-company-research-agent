"""Canonical financial metrics from parsed iXBRL facts.

Taxonomy concepts vary by accounts type and software; each metric lists the concepts
that mean it, in priority order. Only entity-level facts (no dimensions) are used:
dimensioned facts are breakdowns (e.g. creditors "within one year").

Micro-entity accounts (the most common small-company filing) report current assets as
one line and usually no cash figure, so net assets and net current assets are the
metrics that are reliably present.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from ukco.financials.ixbrl import IxbrlDocument

EXTRACTOR_VERSION = "ixbrl-1"

METRICS: dict[str, tuple[str, ...]] = {
    "net_assets": (
        "NetAssetsLiabilities",
        "NetAssetsLiabilitiesIncludingPensionAssetLiability",
        "Equity",
    ),
    "net_current_assets": ("NetCurrentAssetsLiabilities",),
    "current_assets": ("CurrentAssets",),
    "total_assets_less_current_liabilities": ("TotalAssetsLessCurrentLiabilities",),
    "fixed_assets": ("FixedAssets",),
    "cash": ("CashBankOnHand", "CashCashEquivalents", "Cash"),
    "turnover": ("TurnoverRevenue", "Revenue"),
    "profit_loss": ("ProfitLoss", "ProfitLossOnOrdinaryActivitiesBeforeTax"),
    "employees": ("AverageNumberEmployeesDuringPeriod",),
}


@dataclass(frozen=True)
class FinancialFact:
    metric: str
    period_start: dt.date | None  # None for balance-sheet (instant) figures
    period_end: dt.date
    value: Decimal
    unit: str | None
    concept: str
    locator: str
    document_id: str


def extract_financials(doc: IxbrlDocument, document_id: str) -> list[FinancialFact]:
    """One fact per (metric, period end): the first concept in priority order that has an
    entity-level value, and its first occurrence in the document (balance sheet before notes)."""
    out: list[FinancialFact] = []
    for metric, concepts in METRICS.items():
        seen: set[dt.date] = set()
        for concept in concepts:
            for fact in doc.find(concept):
                end = fact.context.end
                if end in seen:
                    continue
                seen.add(end)
                out.append(
                    FinancialFact(
                        metric=metric,
                        period_start=fact.context.start,
                        period_end=end,
                        value=fact.value,
                        unit=fact.unit,
                        concept=f"{fact.prefix}:{fact.concept}",
                        locator=fact.locator,
                        document_id=document_id,
                    )
                )
    return sorted(out, key=lambda f: (f.metric, f.period_end))
