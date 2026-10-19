"""Run every rule over a bundle and score the result."""

import datetime as dt

from ukco.evidence import EvidenceIndex
from ukco.ingest.company import CompanyBundle
from ukco.risk.models import Assessment, RuleOutcome, ScoreContribution
from ukco.risk.policy import Policy, default_policy
from ukco.risk.rules import RULES, RuleContext

MAX_SCORE = 100


def assess(
    bundle: CompanyBundle,
    evidence: EvidenceIndex,
    *,
    as_of: dt.date | None = None,
    policy: Policy | None = None,
) -> Assessment:
    policy = policy or default_policy()
    as_of = as_of or bundle.fetched_at.date()
    created = bundle.profile.date_of_creation
    if created is None or as_of < created:
        raise ValueError(f"as_of {as_of} is before the company existed ({created})")
    if as_of > bundle.fetched_at.date():
        raise ValueError(
            f"as_of {as_of} is after the data was fetched ({bundle.fetched_at:%Y-%m-%d})"
        )

    ctx = RuleContext(bundle=bundle, evidence=evidence, as_of=as_of, policy=policy)
    outcomes: list[RuleOutcome] = [rule(ctx) for rule in RULES]
    contributions = [
        ScoreContribution(flag_type=f.flag_type, severity=f.severity, points=f.points)
        for o in outcomes
        for f in o.flags
    ]
    return Assessment(
        company_number=bundle.company_number,
        company_name=bundle.profile.company_name,
        as_of=as_of,
        live=ctx.live,
        policy_version=policy.version,
        policy_sha256=policy.sha256,
        outcomes=outcomes,
        risk_score=MAX_SCORE
        if any(f.details.get("knockout") for o in outcomes for f in o.flags)
        else min(MAX_SCORE, sum(c.points for c in contributions)),
        contributions=contributions,
    )
