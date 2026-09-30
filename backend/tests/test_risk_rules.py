"""Rules engine on real (pseudonymised) small-company fixtures, mutated per scenario.

13237895 ("active") really did file late once: its confirmation statement made up to
2025-03-01 was due 2025-03-15 and filed 2025-09-27. A back-test inside that gap must catch
it; a live check today must not (it has since caught up).
"""

import copy
import datetime as dt
from typing import Any

import pytest

from tests.fakes import ACTIVE, GREGGS, LIQUIDATION, TODAY, FakeCompaniesHouse
from tests.ixbrl_builder import micro_accounts
from ukco.companies_house.models import CompanyProfile
from ukco.evidence import EvidenceIndex, StoredResponse, resolve_pointer
from ukco.financials.ixbrl import parse_ixbrl
from ukco.ingest.company import CompanyBundle, fetch_bundle
from ukco.risk import Assessment, RuleOutcome, assess, default_policy, load_policy
from ukco.risk.deadlines import accounts_due, add_months, confirmation_statement_due
from ukco.risk.policy import DEFAULT_POLICY

pytestmark = pytest.mark.anyio

FETCHED = dt.datetime(2026, 9, 28, 12, tzinfo=dt.UTC)


async def build(
    ch: FakeCompaniesHouse, number: str, *, network: bool = True
) -> tuple[CompanyBundle, EvidenceIndex]:
    async with ch.client() as client:
        bundle = await fetch_bundle(client, number, network=network, today=TODAY)
    bundle.fetched_at = FETCHED  # "live" means as of this fixed date
    evidence = EvidenceIndex.build(
        StoredResponse(i + 1, r.path, r.params, r.body) for i, r in enumerate(bundle.responses)
    )
    return bundle, evidence


async def run(ch: FakeCompaniesHouse, number: str, as_of: dt.date | None = None) -> Assessment:
    bundle, evidence = await build(ch, number)
    result = assess(bundle, evidence, as_of=as_of)
    assert_citations_resolve(result, bundle)
    return result


def assert_citations_resolve(result: Assessment, bundle: CompanyBundle) -> None:
    bodies = {i + 1: r.body for i, r in enumerate(bundle.responses)}
    documents = {d.document_id: d for d in bundle.accounts}
    for flag in result.flags:
        for c in flag.evidence:
            if c.kind == "api":
                assert c.snapshot_id is not None
                resolve_pointer(bodies[c.snapshot_id], c.pointer)  # raises if it doesn't resolve
            else:
                doc = documents[c.document_id or ""]
                assert doc.content, "document citation without stored content"
                locators = {f.locator for f in parse_ixbrl(doc.content).facts}
                assert c.pointer in locators, f"{c.pointer} not in document {c.document_id}"


def outcome(result: Assessment, rule: str) -> RuleOutcome:
    return next(o for o in result.outcomes if o.rule == rule)


def edit(ch: FakeCompaniesHouse, path: str) -> Any:
    """Deep-copied, editable route body (fixtures are shared across tests)."""
    ch.routes[path] = copy.deepcopy(ch.routes[path])
    return ch.routes[path]


def director(oid: str, appointed: str, resigned: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": f"PERSON {oid}",
        "officer_role": "director",
        "appointed_on": appointed,
        "links": {"officer": {"appointments": f"/officers/{oid}/appointments"}},
    }
    if resigned:
        item["resigned_on"] = resigned
    return item


# --- Deadlines ------------------------------------------------------------------------


def test_add_months_keeps_month_ends() -> None:
    assert add_months(dt.date(2025, 1, 31), 1) == dt.date(2025, 2, 28)
    assert add_months(dt.date(2024, 2, 29), 12) == dt.date(2025, 2, 28)
    assert add_months(dt.date(2025, 4, 5), 9) == dt.date(2026, 1, 5)
    assert add_months(dt.date(2025, 3, 1), -12) == dt.date(2024, 3, 1)


def test_computed_deadlines_match_the_register() -> None:
    """Rule fidelity: our deadline maths agrees with Companies House's own."""
    ch = FakeCompaniesHouse()
    small = CompanyProfile.model_validate(ch.routes[f"/company/{ACTIVE}"])
    assert small.accounts and small.accounts.last_accounts and small.date_of_creation
    _, due = accounts_due(
        small.type, small.date_of_creation, small.accounts.last_accounts.made_up_to
    )
    assert due == small.accounts.next_due
    cs = small.confirmation_statement
    assert (
        cs
        and confirmation_statement_due(small.date_of_creation, cs.last_made_up_to)[1] == cs.next_due
    )

    greggs = CompanyProfile.model_validate(ch.routes[f"/company/{GREGGS}"])  # plc, 52-week year
    assert greggs.accounts and greggs.accounts.last_accounts and greggs.accounts.next_due
    assert greggs.date_of_creation
    _, due = accounts_due(
        greggs.type, greggs.date_of_creation, greggs.accounts.last_accounts.made_up_to
    )
    assert abs((due - greggs.accounts.next_due).days) <= 7  # 52/53-week periods: ±7 days


# --- Whole-company outcomes ---------------------------------------------------------------


async def test_healthy_small_company_scores_zero_live() -> None:
    result = await run(FakeCompaniesHouse(), ACTIVE)

    assert result.live and result.as_of == FETCHED.date()
    assert result.risk_score == 0 and result.flags == []
    statuses = {o.rule: o.status for o in result.outcomes}
    assert statuses["overdue_accounts"] == "clear"
    assert statuses["financial_deterioration"] == "not_assessed"
    assert all(o.note for o in result.outcomes if o.status != "flagged")  # always say why


async def test_missing_accounts_are_caught_in_a_back_test() -> None:
    ch = FakeCompaniesHouse()
    history = edit(ch, f"/company/{ACTIVE}/filing-history")
    # Pretend the accounts made up to 2025-04-05 were never filed.
    history["items"] = [
        f
        for f in history["items"]
        if not (f["type"] == "AA" and f["description_values"]["made_up_date"] == "2025-04-05")
    ]

    result = await run(ch, ACTIVE, as_of=dt.date(2026, 3, 1))
    accounts = outcome(result, "overdue_accounts")
    assert accounts.status == "flagged"
    flag = accounts.flags[0]
    assert flag.details["method"] == "computed"
    assert flag.details["last_made_up_to"] == "2024-04-05"
    assert flag.details["due"] == "2026-01-05" and flag.details["days_overdue"] == 55
    assert flag.severity == "medium"
    assert not result.live


async def test_late_confirmation_statement_back_test() -> None:
    result = await run(FakeCompaniesHouse(), ACTIVE, as_of=dt.date(2025, 9, 1))

    cs = outcome(result, "overdue_confirmation_statement")
    assert cs.status == "flagged"
    assert cs.flags[0].details["due"] == "2025-03-15"
    assert cs.flags[0].severity == "medium"  # 170 days late


async def test_live_overdue_uses_the_register_and_cites_it() -> None:
    ch = FakeCompaniesHouse()
    profile = edit(ch, f"/company/{ACTIVE}")
    profile["accounts"].update({"overdue": True, "next_due": "2026-05-01"})

    result = await run(ch, ACTIVE)
    flag = outcome(result, "overdue_accounts").flags[0]
    assert flag.details["method"] == "register"
    assert flag.severity == "high"  # 150 days
    assert flag.evidence[0].pointer == "/accounts"
    assert result.risk_score == default_policy().points["high"]


async def test_companies_in_liquidation_are_not_assessed_for_filing_deadlines() -> None:
    result = await run(FakeCompaniesHouse(), LIQUIDATION)

    assert outcome(result, "overdue_accounts").status == "not_assessed"
    assert "liquidation" in (outcome(result, "overdue_accounts").note or "")
    before_liquidation = await run(FakeCompaniesHouse(), LIQUIDATION, as_of=dt.date(2024, 4, 1))
    assert outcome(before_liquidation, "overdue_accounts").status == "clear"


# --- Director churn ---------------------------------------------------------------------


async def test_director_churn_counts_real_changes_only() -> None:
    ch = FakeCompaniesHouse()
    officers = edit(ch, f"/company/{ACTIVE}/officers")
    officers["items"] += [
        director("ChurnA000001", "2026-01-10", "2026-06-01"),  # 2 events
        director("ChurnB000001", "2026-03-01"),  # 1 event
        director("ChurnC000001", "2025-12-01", "2026-04-15"),  # 2 events
        # Same person resigning and being re-appointed the same day: a role change.
        director("RoleChange01", "2020-01-01", "2026-02-01"),
        director("RoleChange01", "2026-02-01"),
        director("Future000001", "2026-10-01"),  # after as_of
    ]

    result = await run(ch, ACTIVE)
    flag = outcome(result, "director_churn").flags[0]
    assert flag.details["changes"] == 5
    assert flag.severity == "high"
    # An officer's appointment and resignation are one register row: 3 officers, 3 citations.
    assert len(flag.evidence) == 3


async def test_initial_board_is_not_churn() -> None:
    ch = FakeCompaniesHouse()
    edit(ch, f"/company/{ACTIVE}/officers")["items"] += [
        director("Initial00001", "2021-03-02"),
        director("Initial00002", "2021-03-10"),
        director("Initial00003", "2021-03-20"),
    ]
    result = await run(ch, ACTIVE, as_of=dt.date(2021, 12, 1))
    assert outcome(result, "director_churn").status == "clear"


# --- PSC and office changes ---------------------------------------------------------------


async def test_psc_changes_in_window() -> None:
    ch = FakeCompaniesHouse()
    pscs = edit(ch, f"/company/{ACTIVE}/persons-with-significant-control")
    existing = pscs["items"][0]
    existing["ceased_on"] = "2026-05-01"
    pscs["items"].append(
        {
            "kind": "corporate-entity-person-with-significant-control",
            "name": "PERSON NEWCO",
            "notified_on": "2026-05-01",
            "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
            "links": {
                "self": f"/company/{ACTIVE}/persons-with-significant-control"
                "/corporate-entity/NewPsc000001"
            },
        }
    )

    result = await run(ch, ACTIVE)
    flag = outcome(result, "psc_changes").flags[0]
    assert flag.details["changes"] == 2 and flag.severity == "low"
    assert {e["event"] for e in flag.details["events"]} == {"new controller", "controller ceased"}


async def test_frequent_office_moves_and_disputed_office() -> None:
    ch = FakeCompaniesHouse()
    history = edit(ch, f"/company/{ACTIVE}/filing-history")
    for i, day in enumerate(("2025-11-01", "2026-02-01", "2026-06-01")):
        history["items"].insert(
            0,
            {
                "transaction_id": f"MoveTxn{i:05d}",
                "type": "AD01",
                "category": "address",
                "date": day,
                "description_values": {"change_date": day, "old_address": "A", "new_address": "B"},
            },
        )
    edit(ch, f"/company/{ACTIVE}")["registered_office_is_in_dispute"] = True

    result = await run(ch, ACTIVE)
    flags = {f.flag_type: f for f in outcome(result, "office_changes").flags}
    assert flags["office_changes"].details["changes"] == 3
    assert flags["office_changes"].severity == "medium"
    assert flags["registered_office_problem"].severity == "high"
    assert (
        flags["registered_office_problem"].evidence[0].pointer == "/registered_office_is_in_dispute"
    )


async def test_disputed_office_marker_is_ignored_in_back_tests() -> None:
    ch = FakeCompaniesHouse()
    edit(ch, f"/company/{ACTIVE}")["registered_office_is_in_dispute"] = True
    result = await run(ch, ACTIVE, as_of=dt.date(2024, 6, 1))
    assert outcome(result, "office_changes").status == "clear"  # undated marker: not usable


# --- New company in a higher-risk sector -------------------------------------------------


async def test_new_company_with_high_risk_sic() -> None:
    ch = FakeCompaniesHouse()
    edit(ch, f"/company/{ACTIVE}")["sic_codes"] = ["70229", "64999"]

    young = await run(ch, ACTIVE, as_of=dt.date(2021, 9, 1))
    flag = outcome(young, "new_company_high_risk_sic").flags[0]
    assert flag.details["sic_codes"] == ["64999"]
    assert "current" in (outcome(young, "new_company_high_risk_sic").note or "")

    older = await run(ch, ACTIVE, as_of=dt.date(2023, 1, 1))
    assert outcome(older, "new_company_high_risk_sic").status == "clear"


# --- Officer network ----------------------------------------------------------------------


def linked(number: str, status: str) -> dict[str, Any]:
    return {
        "appointed_to": {
            "company_number": number,
            "company_name": f"CO {number}",
            "company_status": status,
        },
        "officer_role": "director",
        "appointed_on": "2019-01-01",
    }


async def test_director_with_many_failed_companies_is_flagged_live_only() -> None:
    ch = FakeCompaniesHouse()
    network = edit(ch, f"/officers/{ch.networked_officer}/appointments")
    network["items"] += [linked(f"9000000{i}", "dissolved") for i in range(4)] + [
        linked("90000010", "liquidation"),
        linked("90000011", "administration"),
        linked("90000012", "active"),
    ]
    network["total_results"] = len(network["items"])

    live = await run(ch, ACTIVE)
    flag = outcome(live, "officer_network").flags[0]
    hit = flag.details["officers"][0]
    assert (hit["adverse"], hit["insolvent"]) == (6, 2)
    assert flag.severity == "high"  # 2 insolvent links
    assert any(c.path.startswith("/officers/") for c in flag.evidence)

    back_test = await run(ch, ACTIVE, as_of=dt.date(2024, 1, 1))
    assert outcome(back_test, "officer_network").status == "not_assessed"


async def test_mostly_healthy_network_is_clear() -> None:
    ch = FakeCompaniesHouse()
    network = edit(ch, f"/officers/{ch.networked_officer}/appointments")
    network["items"] += [linked(f"8000000{i}", "dissolved") for i in range(5)] + [
        linked(f"8100000{i}", "active") for i in range(6)
    ]
    result = await run(ch, ACTIVE)
    assert outcome(result, "officer_network").status == "clear"  # 5 of 11 < 50%


# --- Financial deterioration -------------------------------------------------------------
# The active company's two latest accounts filings: made up to 2025-04-05 (filed 2025-04-15)
# and to 2024-04-05 (filed 2024-04-30). Each iXBRL filing carries the prior year too.


def set_accounts(
    ch: FakeCompaniesHouse, latest: bytes | None, previous: bytes | None = None
) -> None:
    newest, older = ch.accounts_document_ids(ACTIVE)[:2]
    ch.documents[newest] = latest
    if previous is not None:
        ch.documents[older] = previous


async def test_sharp_fall_in_assets_is_flagged_with_document_citations() -> None:
    ch = FakeCompaniesHouse()
    set_accounts(
        ch,
        micro_accounts(
            "2025-04-05",
            "2024-04-05",
            now={
                "NetAssetsLiabilities": 8_000,
                "NetCurrentAssetsLiabilities": 5_000,
                "CurrentAssets": 9_000,
            },
            before={
                "NetAssetsLiabilities": 40_000,
                "NetCurrentAssetsLiabilities": 30_000,
                "CurrentAssets": 50_000,
            },
        ),
    )

    result = await run(ch, ACTIVE)
    fin = outcome(result, "financial_deterioration")
    assert fin.status == "flagged"
    (flag,) = fin.flags
    assert flag.flag_type == "financial_deterioration" and flag.severity == "medium"
    assert "net assets £40,000 → £8,000 (80% down)" in flag.explanation
    assert all(c.kind == "document" for c in flag.evidence)
    assert flag.evidence[0].url.endswith("/document?format=xhtml&download=0")


async def test_net_assets_turning_negative_is_high() -> None:
    ch = FakeCompaniesHouse()
    set_accounts(
        ch,
        micro_accounts(
            "2025-04-05",
            "2024-04-05",
            now={"NetAssetsLiabilities": -12_000},
            before={"NetAssetsLiabilities": 15_000},
        ),
    )

    result = await run(ch, ACTIVE)
    flags = {f.flag_type: f for f in outcome(result, "financial_deterioration").flags}
    assert flags["net_liabilities"].severity == "high"
    assert flags["net_liabilities"].title == "Net assets turned negative"
    assert "-£12,000" in flags["net_liabilities"].explanation
    assert result.risk_score == 30 + 15  # plus the drop itself


async def test_small_base_swings_are_ignored() -> None:
    ch = FakeCompaniesHouse()
    set_accounts(
        ch,
        micro_accounts(
            "2025-04-05",
            "2024-04-05",
            now={"NetAssetsLiabilities": 100},
            before={"NetAssetsLiabilities": 3_000},
        ),
    )
    fin = outcome(await run(ch, ACTIVE), "financial_deterioration")
    assert fin.status == "clear" and "£100" in (fin.note or "")


async def test_pdf_only_accounts_are_not_assessed() -> None:
    ch = FakeCompaniesHouse()
    set_accounts(ch, None, None)  # both PDF
    fin = outcome(await run(ch, ACTIVE), "financial_deterioration")
    assert fin.status == "not_assessed" and "PDF-only" in (fin.note or "")


async def test_back_test_only_sees_accounts_filed_by_as_of() -> None:
    ch = FakeCompaniesHouse()
    healthy = micro_accounts(
        "2024-04-05",
        "2023-04-05",
        now={"NetAssetsLiabilities": 50_000},
        before={"NetAssetsLiabilities": 45_000},
    )
    collapsed = micro_accounts(
        "2025-04-05",
        "2024-04-05",
        now={"NetAssetsLiabilities": -5_000},
        before={"NetAssetsLiabilities": 50_000},
    )
    set_accounts(ch, collapsed, healthy)

    live = await run(ch, ACTIVE)
    assert outcome(live, "financial_deterioration").status == "flagged"
    before_filing = await run(ch, ACTIVE, as_of=dt.date(2025, 1, 1))  # 2025 accounts not yet filed
    fin = outcome(before_filing, "financial_deterioration")
    assert fin.status == "clear" and "£50,000 at 2024-04-05" in (fin.note or "")


# --- Engine and policy ----------------------------------------------------------------------


async def test_as_of_must_be_within_the_company_lifetime_and_data() -> None:
    bundle, evidence = await build(FakeCompaniesHouse(), ACTIVE)
    with pytest.raises(ValueError, match="before the company existed"):
        assess(bundle, evidence, as_of=dt.date(2020, 1, 1))
    with pytest.raises(ValueError, match="after the data was fetched"):
        assess(bundle, evidence, as_of=dt.date(2027, 1, 1))


async def test_score_is_capped_at_100() -> None:
    ch = FakeCompaniesHouse()
    profile = edit(ch, f"/company/{ACTIVE}")
    profile["accounts"].update({"overdue": True, "next_due": "2026-01-01"})
    profile["confirmation_statement"].update({"overdue": True, "next_due": "2026-01-01"})
    profile["registered_office_is_in_dispute"] = True
    profile["undeliverable_registered_office_address"] = True

    result = await run(ch, ACTIVE)
    assert sum(c.points for c in result.contributions) > 100
    assert result.risk_score == 100


def test_policy_is_strict_and_hashed(tmp_path) -> None:
    policy = default_policy()
    assert len(policy.sha256) == 64 and policy.version == 1
    bad = tmp_path / "policy.yaml"
    bad.write_text(DEFAULT_POLICY.read_text() + "\nunexpected_key: 1\n")
    with pytest.raises(ValueError):
        load_policy(bad)
