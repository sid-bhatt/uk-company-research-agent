"""iXBRL parsing and financial metric extraction."""

import datetime as dt
from decimal import Decimal

import pytest

from tests.ixbrl_builder import Tag, make_ixbrl, micro_accounts
from ukco.financials.extract import extract_financials
from ukco.financials.ixbrl import IxbrlError, parse_ixbrl


def test_parses_values_signs_scales_and_formats() -> None:
    doc = parse_ixbrl(
        make_ixbrl(
            [
                Tag("NetAssetsLiabilities", "2025-03-31", "12,345"),
                Tag("NetAssetsLiabilities", "2024-03-31", "(2,000)", {"sign": "-"}),
                Tag("CurrentAssets", "2025-03-31", "1.5", {"scale": "3"}),
                Tag("FixedAssets", "2025-03-31", "-", {"format": "ixt:fixed-zero"}),
                Tag("TurnoverRevenue", "2025-03-31", "98,000"),
            ]
        )
    )
    values = {(f.concept, str(f.context.end)): f.value for f in doc.facts}
    assert values[("NetAssetsLiabilities", "2025-03-31")] == Decimal(12345)
    assert values[("NetAssetsLiabilities", "2024-03-31")] == Decimal(-2000)
    assert values[("CurrentAssets", "2025-03-31")] == Decimal(1500)
    assert values[("FixedAssets", "2025-03-31")] == 0
    turnover = next(f for f in doc.facts if f.concept == "TurnoverRevenue")
    assert turnover.context.start == dt.date(2024, 4, 1)  # a duration, not an instant


def test_locators_prefer_ids_and_disambiguate_repeats() -> None:
    doc = parse_ixbrl(
        make_ixbrl(
            [
                Tag("Equity", "2025-03-31", "10", {"id": "fact-42"}),
                Tag("CurrentAssets", "2025-03-31", "5"),
                Tag("CurrentAssets", "2025-03-31", "5"),  # balance sheet + notes
            ]
        )
    )
    locators = [f.locator for f in doc.facts]
    assert locators[0] == "#fact-42"
    assert locators[1].endswith("[1]") and locators[2].endswith("[2]")


def test_dimensioned_breakdowns_are_not_entity_totals() -> None:
    doc = parse_ixbrl(
        make_ixbrl(
            [
                Tag("Creditors", "2025-03-31", "7,000"),
                Tag(
                    "Creditors",
                    "2025-03-31",
                    "4,000",
                    dimension=("MaturitiesOrExpirationPeriodsDimension", "WithinOneYear"),
                ),
            ]
        )
    )
    assert [f.value for f in doc.find("Creditors")] == [Decimal(7000)]
    assert len(doc.find("Creditors", dimensionless=False)) == 2


def test_extract_prefers_net_assets_over_equity_and_keeps_both_years() -> None:
    content = micro_accounts(
        "2025-03-31",
        "2024-03-31",
        now={"NetAssetsLiabilities": 5000, "Equity": 5000, "CurrentAssets": 20000},
        before={"NetAssetsLiabilities": 30000, "CurrentAssets": 45000},
    )
    facts = extract_financials(parse_ixbrl(content), "DocId00000001")
    net = [(str(f.period_end), f.value, f.concept) for f in facts if f.metric == "net_assets"]
    assert net == [
        ("2024-03-31", 30000, "core:NetAssetsLiabilities"),
        ("2025-03-31", 5000, "core:NetAssetsLiabilities"),
    ]
    assert {f.metric for f in facts} == {"net_assets", "current_assets"}
    assert all(f.document_id == "DocId00000001" for f in facts)


def test_equity_is_the_fallback_for_net_assets() -> None:
    facts = extract_financials(parse_ixbrl(make_ixbrl([Tag("Equity", "2025-03-31", "900")])), "D")
    assert [(f.metric, f.concept) for f in facts] == [("net_assets", "core:Equity")]


def test_rejects_non_ixbrl() -> None:
    with pytest.raises(IxbrlError):
        parse_ixbrl(b"%PDF-1.7 not xml")
    with pytest.raises(IxbrlError):
        parse_ixbrl(b"<html xmlns='http://www.w3.org/1999/xhtml'><body>no tags</body></html>")
