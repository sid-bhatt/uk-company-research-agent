"""Ground-truth outcomes for point-in-time evaluation.

Question: scored as of date T using only what was known then, did the risk score rank
companies that went on to have an adverse event in the next 18 months above those that
didn't?

Adverse events (from the register itself, each with the record that proves it):
- compulsory_strike_off: a `GAZ1` first-gazette notice (Companies House moving to strike the
  company off, usually for not filing). `GAZ1(A)` is the *voluntary* equivalent: not adverse.
  A later `DISS40` (strike-off halted because the company caught up) still counts: the
  company fell far enough behind to be gazetted.
- insolvency: an insolvency case other than a members' voluntary liquidation (MVL is a
  solvent wind-down).

Non-adverse: voluntary_closure (DS01 / GAZ1(A)) or survived.

Eligibility at T: incorporated at least 12 months before T (so there is a filing history),
not dissolved by T, and no strike-off notice, closure or insolvency on or before T. The
population is "companies that looked alive and clean at T".
"""

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from ukco.companies_house.models import (
    CompanyProfile,
    FilingHistoryItem,
    Insolvency,
    InsolvencyCase,
)
from ukco.risk.deadlines import add_months

Outcome = Literal["compulsory_strike_off", "insolvency", "voluntary_closure", "survived"]
ADVERSE: tuple[Outcome, ...] = ("compulsory_strike_off", "insolvency")
WINDOW_MONTHS = 18
MIN_AGE_MONTHS = 12
SOLVENT_CASE_TYPES = {"members-voluntary-liquidation"}
VOLUNTARY_CLOSURE_TYPES = {"DS01", "GAZ1(A)"}
DISSOLVED_TYPES = {"GAZ2", "GAZ2(A)"}


@dataclass(frozen=True)
class Label:
    eligible: bool
    reason: str  # why ineligible, or how the outcome was decided
    outcome: Outcome | None = None
    event_date: dt.date | None = None
    evidence: str | None = None  # filing transaction id or insolvency case type + number

    @property
    def adverse(self) -> bool:
        return self.outcome in ADVERSE


def case_start(case: InsolvencyCase) -> dt.date | None:
    dates = []
    for entry in case.dates:
        raw = entry.get("date")
        if isinstance(raw, str):
            dates.append(dt.date.fromisoformat(raw[:10]))
    return min(dates) if dates else None


def label_company(
    profile: CompanyProfile,
    filings: list[FilingHistoryItem],
    insolvency: Insolvency,
    as_of: dt.date,
    *,
    window_months: int = WINDOW_MONTHS,
) -> Label:
    end = add_months(as_of, window_months)
    created = profile.date_of_creation
    if created is None or created > add_months(as_of, -MIN_AGE_MONTHS):
        return Label(False, f"incorporated {created}: under {MIN_AGE_MONTHS} months old at {as_of}")
    if profile.date_of_cessation and profile.date_of_cessation <= as_of:
        return Label(False, f"dissolved {profile.date_of_cessation}, before {as_of}")

    dated = [f for f in filings if f.date and f.type]
    gazetted = [f for f in dated if f.type == "GAZ1"]
    closures = [f for f in dated if f.type in VOLUNTARY_CLOSURE_TYPES | DISSOLVED_TYPES]
    cases = [
        (start, case)
        for case in insolvency.cases
        if case.type not in SOLVENT_CASE_TYPES and (start := case_start(case))
    ]

    before = [f"{f.type} {f.date}" for f in gazetted + closures if f.date and f.date <= as_of]
    before += [f"{c.type} {s}" for s, c in cases if s <= as_of]
    if before:
        return Label(False, f"already closing or insolvent by {as_of}: {', '.join(sorted(before))}")

    events: list[tuple[dt.date, Outcome, str]] = []
    for f in gazetted:
        if f.date and as_of < f.date <= end:
            events.append((f.date, "compulsory_strike_off", f"filing {f.transaction_id} (GAZ1)"))
    for start, case in cases:
        if as_of < start <= end:
            events.append((start, "insolvency", f"case {case.number}: {case.type}"))
    if events:
        date, outcome, evidence = min(events, key=lambda e: e[0])
        return Label(True, f"{outcome} on {date}", outcome, date, evidence)

    voluntary = [
        f
        for f in closures
        if f.date and as_of < f.date <= end and f.type in VOLUNTARY_CLOSURE_TYPES
    ]
    if voluntary:
        first = min(voluntary, key=lambda f: f.date or dt.date.max)
        return Label(
            True,
            f"voluntary closure {first.type} on {first.date}",
            "voluntary_closure",
            first.date,
            f"filing {first.transaction_id} ({first.type})",
        )
    return Label(True, f"no adverse event by {end}", "survived")
