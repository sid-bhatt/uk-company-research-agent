"""Build a frozen eval snapshot from the live API (run once; CI replays it offline).

Sampling: random 3-day incorporation windows (2020 to Sep 2023) crossed with three
sources (companies dissolved in the outcome window, companies in insolvency, active
companies). Every
candidate is labelled from its own register records (labels.py); the source only helps
find each outcome. Candidates are kept until each outcome's quota is met, so the set is
*stratified*: rank metrics (AUC) are valid, prevalence-dependent ones (precision) are not.
"""

import datetime as dt
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, field

from ukco.companies_house import CompaniesHouseClient, CompaniesHouseError
from ukco.companies_house.models import CompanyProfile
from ukco.evals.frozen import FrozenCompany
from ukco.evals.labels import WINDOW_MONTHS, Outcome, label_company
from ukco.ingest.company import fetch_bundle
from ukco.risk.deadlines import add_months

log = logging.getLogger(__name__)

AS_OF = dt.date(2024, 9, 30)
DEFAULT_QUOTAS: dict[Outcome, int] = {
    "compulsory_strike_off": 20,
    "insolvency": 12,
    "voluntary_closure": 8,
    "survived": 20,
}
INCORPORATED_FROM, INCORPORATED_TO = dt.date(2020, 1, 1), dt.date(2023, 9, 30)


def scaled_quotas(size: int) -> dict[Outcome, int]:
    """DEFAULT_QUOTAS' proportions at another size (largest remainder, so they sum to size)."""
    total = sum(DEFAULT_QUOTAS.values())
    exact: dict[Outcome, float] = {o: size * n / total for o, n in DEFAULT_QUOTAS.items()}
    quotas: dict[Outcome, int] = {o: int(x) for o, x in exact.items()}
    by_remainder = sorted(exact, key=lambda o: exact[o] - quotas[o], reverse=True)
    for o in by_remainder[: size - sum(quotas.values())]:
        quotas[o] += 1
    return quotas


@dataclass
class BuildStats:
    candidates: int = 0
    ineligible: Counter[str] = field(default_factory=Counter)
    over_quota: int = 0
    errors: int = 0
    seconds_per_company: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Source:
    """Where to look for candidates likely to have some outcomes (labels decide)."""

    outcomes: frozenset[Outcome]
    status: tuple[str, ...]
    dissolved_from: dt.date | None = None
    dissolved_to: dt.date | None = None


def _sources(as_of: dt.date) -> dict[str, Source]:
    end = add_months(as_of, WINDOW_MONTHS)
    return {
        # Dissolution follows a strike-off notice by ~2+ months, hence the later end date.
        "dissolved_in_window": Source(
            frozenset({"compulsory_strike_off", "voluntary_closure"}),
            ("dissolved",),
            as_of,
            add_months(end, 4),
        ),
        "insolvent": Source(frozenset({"insolvency"}), ("liquidation", "administration")),
        "active": Source(frozenset({"survived"}), ("active",)),
    }


async def build_eval_set(
    client: CompaniesHouseClient,
    *,
    as_of: dt.date = AS_OF,
    quotas: dict[Outcome, int] | None = None,
    seed: int = 7,
    max_candidates: int = 800,
    exclude: frozenset[str] = frozenset(),
) -> tuple[list[FrozenCompany], BuildStats]:
    """`exclude`: company numbers already in other eval sets, so the sets never overlap."""
    today = dt.date.today()
    if add_months(as_of, WINDOW_MONTHS) > today:
        raise ValueError("the outcome window hasn't finished yet: survivors can't be labelled")
    remaining = dict(quotas or DEFAULT_QUOTAS)
    rng = random.Random(seed)
    sources = _sources(as_of)
    stats = BuildStats()
    seen: set[str] = set(exclude)
    kept: list[FrozenCompany] = []

    while any(remaining.values()) and stats.candidates < max_candidates:
        open_sources = [n for n, src in sources.items() if any(remaining[o] for o in src.outcomes)]
        source = rng.choice(open_sources)
        src = sources[source]
        start = INCORPORATED_FROM + dt.timedelta(
            days=rng.randrange((INCORPORATED_TO - INCORPORATED_FROM).days)
        )
        page = await client.advanced_search(
            incorporated_from=start,
            incorporated_to=start + dt.timedelta(days=2),
            company_type=["ltd"],
            company_status=list(src.status),
            dissolved_from=src.dissolved_from,
            dissolved_to=src.dissolved_to,
            size=100,
        )
        items = [i for i in page.items if i.company_number not in seen]
        rng.shuffle(items)
        for item in items[:5]:  # a few per window keeps the sample spread across dates
            seen.add(item.company_number)
            stats.candidates += 1
            started = time.perf_counter()
            try:
                profile = CompanyProfile.model_validate(
                    await client.get_company_raw(item.company_number)
                )
                filings = [f async for f in client.iter_filing_history(item.company_number)]
                label = label_company(
                    profile, filings, await client.get_insolvency(item.company_number), as_of
                )
                if not label.eligible or label.outcome is None:
                    stats.ineligible[label.reason.split(":")[0].split(" ")[0]] += 1
                    continue
                if remaining[label.outcome] <= 0:
                    stats.over_quota += 1
                    continue
                bundle = await fetch_bundle(
                    client, item.company_number, network=False, accounts_as_of=as_of
                )
            except CompaniesHouseError as exc:
                log.warning("%s: %s", item.company_number, exc)
                stats.errors += 1
                continue
            remaining[label.outcome] -= 1
            stats.seconds_per_company.append(time.perf_counter() - started)
            kept.append(
                FrozenCompany.from_bundle(
                    bundle,
                    stratum=source,
                    outcome=label.outcome,
                    event_date=label.event_date,
                    evidence=label.evidence,
                )
            )
            log.info(
                "kept %s %s (%s left)", item.company_number, label.outcome, sum(remaining.values())
            )
            if not any(remaining.values()):
                break
    return kept, stats
