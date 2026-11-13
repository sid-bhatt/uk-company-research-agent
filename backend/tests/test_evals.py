"""Eval machinery: outcome labels, metrics, and frozen-snapshot replay."""

import datetime as dt
from pathlib import Path

import pytest

from tests.fakes import ACTIVE, TODAY, FakeCompaniesHouse
from ukco.companies_house.models import CompanyProfile, FilingHistoryItem, Insolvency
from ukco.evals.build import DEFAULT_QUOTAS, scaled_quotas
from ukco.evals.frozen import FrozenCompany, read_snapshot, replay_bundle, write_snapshot
from ukco.evals.labels import label_company
from ukco.evals.metrics import auc, bootstrap_auc_ci
from ukco.evidence import EvidenceIndex, StoredResponse
from ukco.ingest.company import fetch_bundle
from ukco.risk import assess

T = dt.date(2024, 9, 30)


def profile(created: str = "2021-03-01", ceased: str | None = None) -> CompanyProfile:
    return CompanyProfile.model_validate(
        {
            "company_number": "12345678",
            "company_name": "X LTD",
            "date_of_creation": created,
            "date_of_cessation": ceased,
        }
    )


def filing(kind: str, date: str) -> FilingHistoryItem:
    return FilingHistoryItem.model_validate(
        {"transaction_id": f"T-{kind}-{date}", "type": kind, "date": date}
    )


def insolvency(kind: str, start: str) -> Insolvency:
    return Insolvency.model_validate(
        {"cases": [{"type": kind, "number": 1, "dates": [{"type": "x", "date": start}]}]}
    )


NONE = Insolvency()


@pytest.mark.parametrize(
    ("filings", "cases", "outcome"),
    [
        ([filing("GAZ1", "2025-06-10")], NONE, "compulsory_strike_off"),
        # Strike-off halted after the company caught up: still an adverse event.
        (
            [filing("GAZ1", "2025-06-10"), filing("DISS40", "2025-07-01")],
            NONE,
            "compulsory_strike_off",
        ),
        (
            [filing("DS01", "2025-02-01"), filing("GAZ1(A)", "2025-02-11")],
            NONE,
            "voluntary_closure",
        ),
        ([], insolvency("creditors-voluntary-liquidation", "2025-10-23"), "insolvency"),
        ([], insolvency("members-voluntary-liquidation", "2025-10-23"), "survived"),  # solvent
        ([filing("GAZ1", "2026-06-01")], NONE, "survived"),  # after the 18-month window
        ([filing("AA", "2025-01-01")], NONE, "survived"),
    ],
    ids=[
        "gaz1",
        "gaz1-then-discontinued",
        "voluntary",
        "cvl",
        "mvl-is-solvent",
        "outside-window",
        "healthy",
    ],
)
def test_outcome_labels(filings, cases, outcome: str) -> None:
    label = label_company(profile(), filings, cases, T)
    assert label.eligible and label.outcome == outcome
    if outcome != "survived":
        assert label.evidence and label.event_date


@pytest.mark.parametrize(
    ("created", "ceased", "filings", "cases", "reason"),
    [
        ("2024-01-01", None, [], NONE, "under 12 months"),
        ("2021-03-01", "2024-05-01", [], NONE, "dissolved"),
        ("2021-03-01", None, [filing("GAZ1", "2024-06-01")], NONE, "already closing"),
        (
            "2021-03-01",
            None,
            [],
            insolvency("compulsory-liquidation", "2024-02-01"),
            "already closing",
        ),
    ],
    ids=["too-young", "dissolved-before", "gazetted-before", "insolvent-before"],
)
def test_ineligible_at_the_eval_date(created, ceased, filings, cases, reason: str) -> None:
    label = label_company(profile(created, ceased), filings, cases, T)
    assert not label.eligible and reason in label.reason


def test_auc() -> None:
    assert auc([3, 2, 1], [True, False, False]) == 1.0
    assert auc([1, 2, 3], [True, False, False]) == 0.0
    assert auc([5, 5], [True, False]) == 0.5  # ties count half
    assert auc([1, 2], [True, True]) is None  # undefined without both classes
    ci = bootstrap_auc_ci([9, 8, 7, 3, 2, 1], [True, True, True, False, False, False])
    assert ci == (1.0, 1.0)


@pytest.mark.anyio
async def test_frozen_snapshot_replays_the_same_assessment(tmp_path: Path) -> None:
    ch = FakeCompaniesHouse()
    async with ch.client() as client:
        live = await fetch_bundle(client, ACTIVE, network=False, financials=False, today=TODAY)
    live.fetched_at = dt.datetime(2026, 9, 28, tzinfo=dt.UTC)
    frozen = FrozenCompany.from_bundle(
        live, stratum="t", outcome="survived", event_date=None, evidence=None
    )

    path = tmp_path / "snapshot.jsonl.gz"
    write_snapshot(path, [frozen])
    (restored,) = read_snapshot(path)
    replayed, evidence = await replay_bundle(restored)

    as_of = dt.date(2025, 9, 1)  # the real late confirmation statement is visible here
    live_evidence = EvidenceIndex.build(
        StoredResponse(i + 1, r.path, r.params, r.body) for i, r in enumerate(live.responses)
    )
    original = assess(live, live_evidence, as_of=as_of)
    again = assess(replayed, evidence, as_of=as_of)
    assert [f.flag_type for f in again.flags] == [f.flag_type for f in original.flags] != []
    assert again.risk_score == original.risk_score
    text = "\n".join(str(r["body"]) for r in restored.responses)
    assert "PERSON 1" in text  # pseudonymised before writing


# --- Eval sets: sizing and the held-out test set -------------------------------------------


@pytest.mark.parametrize("size", [60, 100, 37, 1])
def test_scaled_quotas_keep_proportions_and_size(size: int) -> None:
    quotas = scaled_quotas(size)
    assert sum(quotas.values()) == size
    for outcome, n in DEFAULT_QUOTAS.items():
        assert abs(quotas[outcome] - size * n / 60) < 1
    assert scaled_quotas(60) == DEFAULT_QUOTAS


def test_held_out_sets_need_a_reason_and_are_never_a_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from ukco import cli

    monkeypatch.setattr(cli, "EVAL_DIR", tmp_path)
    runner = CliRunner()
    for args, message in [
        (["eval", "run", "--name", "test-v9"], "--reason"),
        (["eval", "run", "--name", "test-v9", "--reason", "x", "--update-baseline"], "never"),
        (["eval", "run", "--name", "test-v9", "--reason", "x", "--check"], "never"),
    ]:
        result = runner.invoke(cli.app, args)
        assert result.exit_code != 0 and message in result.output, args


def test_frozen_sets_are_never_rebuilt_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from ukco import cli

    monkeypatch.setattr(cli, "EVAL_DIR", tmp_path)
    (tmp_path / "data" / "v0").mkdir(parents=True)
    (tmp_path / "data" / "v0" / "snapshot.jsonl.gz").write_bytes(b"")
    result = CliRunner().invoke(cli.app, ["eval", "build", "--name", "v0"])
    assert result.exit_code != 0 and "frozen" in result.output
