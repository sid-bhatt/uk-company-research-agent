"""HTTP API end to end: jobs, progress events, reports, reviews and the audit trail."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import load_fixture
from tests.fakes import ACTIVE
from tests.test_report import troubled_company
from ukco.api.app import create_app
from ukco.report import store

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def api(engine: Engine) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    ch = troubled_company()
    ch.routes["/search/companies"] = load_fixture("search_greggs")
    async with ch.client() as client:
        app = create_app(engine=engine, client=client, writer=None, use_settings_writer=False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, app


async def create_and_finish(http: httpx.AsyncClient, app: FastAPI, **body: Any) -> dict[str, Any]:
    created = await http.post("/reports", json={"company_number": ACTIVE, **body})
    assert created.status_code == 202, created.text
    await app.state.runner.drain()
    detail = await http.get(created.json()["links"]["self"])
    assert detail.status_code == 200
    return detail.json()


async def test_report_job_runs_to_a_stored_report(api) -> None:
    http, app = api
    detail = await create_and_finish(http, app)

    assert detail["status"] == "succeeded" and detail["requested_by"] == "local-dev"
    assert detail["risk_score"] == 14  # late filing 10 + financial deterioration 4
    report = detail["report"]
    assert report["company"]["company_number"] == ACTIVE
    assert [f["id"] for f in report["flags"]] == ["F1", "F2"]
    assert detail["decisions"] == {}

    listed = (await http.get("/reports", params={"company_number": ACTIVE})).json()
    assert [r["id"] for r in listed] == [detail["id"]] and "report" not in listed[0]
    markdown = await http.get(f"/reports/{detail['id']}/markdown")
    assert markdown.status_code == 200 and "# KYB report" in markdown.text


async def test_progress_events_stream_every_stage(api) -> None:
    http, app = api
    detail = await create_and_finish(http, app)

    async with http.stream("GET", f"/reports/{detail['id']}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in response.aiter_text()])
    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    stages = [e.get("stage", e["event"]) for e in events]
    assert stages == [
        "queued",
        "fetching",
        "storing_evidence",
        "assessing",
        "validating",
        "succeeded",
    ]
    assert events[-1]["risk_score"] == 14


async def test_invalid_requests_are_rejected(api) -> None:
    http, _ = api
    assert (await http.post("/reports", json={"company_number": "../../etc"})).status_code == 422
    future = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    assert (
        await http.post("/reports", json={"company_number": ACTIVE, "as_of": future})
    ).status_code == 422
    assert (
        await http.post("/reports", json={"company_number": ACTIVE, "surprise": 1})
    ).status_code == 422
    assert (await http.get("/reports/999999")).status_code == 404


async def test_unknown_company_fails_cleanly_and_cannot_be_reviewed(api) -> None:
    http, app = api
    created = await http.post("/reports", json={"company_number": "99999999"})
    await app.state.runner.drain()
    detail = (await http.get(f"/reports/{created.json()['id']}")).json()

    assert detail["status"] == "failed" and "not found" in detail["error"].lower()
    review = await http.post(f"/reports/{detail['id']}/reviews", json={"decision": "approve"})
    assert review.status_code == 409
    assert (await http.get(f"/reports/{detail['id']}/markdown")).status_code == 409


async def test_reviews_build_an_append_only_audit_trail(api, engine: Engine) -> None:
    http, app = api
    rid = (await create_and_finish(http, app))["id"]
    reviews = f"/reports/{rid}/reviews"
    analyst = {"X-User": "analyst@example.com"}

    first = await http.post(
        reviews,
        json={"flag_id": "F1", "decision": "confirm", "note": "Checked accounts"},
        headers=analyst,
    )
    assert first.status_code == 201 and first.json()["target"] == "F1"
    await http.post(
        reviews,
        json={"flag_id": "F1", "decision": "dismiss", "note": "Explained by dividend"},
        headers=analyst,
    )
    await http.post(reviews, json={"flag_id": "F2", "decision": "confirm"}, headers=analyst)
    await http.post(reviews, json={"decision": "approve"}, headers={"X-User": "lead@example.com"})

    trail = (await http.get(reviews)).json()
    assert [(r["target"], r["decision"], r["reviewer"]) for r in trail] == [
        ("F1", "confirm", "analyst@example.com"),
        ("F1", "dismiss", "analyst@example.com"),
        ("F2", "confirm", "analyst@example.com"),
        ("report", "approve", "lead@example.com"),
    ]
    assert all(r["rules_version"] for r in trail)  # each decision records what was reviewed

    decisions = (await http.get(f"/reports/{rid}")).json()["decisions"]
    assert decisions["F1"]["decision"] == "dismiss"  # latest wins; history kept
    assert decisions["report"]["decision"] == "approve"

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE reviews SET decision = 'confirm'"))
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM reviews"))
    assert len((await http.get(reviews)).json()) == 4


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"flag_id": "F9", "decision": "confirm"}, 404),
        ({"flag_id": "F1", "decision": "approve"}, 422),
        ({"decision": "confirm"}, 422),
        ({"decision": "maybe"}, 422),
    ],
    ids=["unknown-flag", "report-decision-on-flag", "flag-decision-on-report", "not-a-decision"],
)
async def test_invalid_reviews(api, body: dict[str, Any], status: int) -> None:
    http, app = api
    rid = (await create_and_finish(http, app))["id"]
    assert (await http.post(f"/reports/{rid}/reviews", json=body)).status_code == status


async def test_jobs_interrupted_by_a_restart_are_marked_failed(engine: Engine) -> None:
    rid = store.create_job(engine, ACTIVE, as_of=None, requested_by="t", params={})
    assert store.fail_interrupted(engine) == 1
    row = store.get_report(engine, rid)
    assert row and row["status"] == "failed" and "restart" in row["error"]


async def test_health(api) -> None:
    http, _ = api
    assert (await http.get("/health")).json()["status"] == "ok"


# --- Auth: the web app's signed tokens -----------------------------------------------

SECRET = "test-signing-secret-at-least-32-bytes!"


def token(**overrides: Any) -> str:
    import time

    import jwt

    now = int(time.time())
    claims = {
        "sub": "github:analyst",
        "iss": "ukco-web",
        "aud": "ukco-api",
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode({**claims, **overrides}, SECRET, algorithm="HS256")


@pytest.fixture
def jwt_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from ukco.config import get_settings

    monkeypatch.setattr(get_settings(), "api_jwt_secret", SecretStr(SECRET))


@pytest.mark.usefixtures("jwt_required")
async def test_signed_token_identifies_the_user(api) -> None:
    http, app = api
    created = await http.post(
        "/reports", json={"company_number": ACTIVE}, headers={"Authorization": f"Bearer {token()}"}
    )
    assert created.status_code == 202
    await app.state.runner.drain()
    detail = await http.get(
        f"/reports/{created.json()['id']}", headers={"Authorization": f"Bearer {token()}"}
    )
    assert detail.json()["requested_by"] == "github:analyst"


@pytest.mark.usefixtures("jwt_required")
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-User": "someone-claiming-to-be-an-analyst"},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": f"Bearer {token(exp=1)}"},
        {"Authorization": f"Bearer {token(aud='someone-else')}"},
        {"Authorization": f"Bearer {token(iss='attacker')}"},
    ],
    ids=["no-token", "x-user-ignored", "garbage", "expired", "wrong-audience", "wrong-issuer"],
)
async def test_requests_without_a_valid_token_are_rejected(api, headers: dict[str, str]) -> None:
    http, _ = api
    assert (await http.get("/reports", headers=headers)).status_code == 401


@pytest.mark.usefixtures("jwt_required")
async def test_token_signed_with_another_secret_is_rejected(api) -> None:
    import time

    import jwt

    http, _ = api
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "x", "iss": "ukco-web", "aud": "ukco-api", "iat": now, "exp": now + 60},
        "a-different-secret-that-is-long-enough",
        algorithm="HS256",
    )
    assert (
        await http.get("/reports", headers={"Authorization": f"Bearer {forged}"})
    ).status_code == 401


async def test_company_search(api) -> None:
    http, _ = api
    hits = (await http.get("/companies/search", params={"q": "greggs"})).json()
    assert hits[0] == {
        "company_number": "00502851",
        "name": "GREGGS PLC",
        "status": "active",
        "incorporated": "1951-12-29",
        "address": hits[0]["address"],
        "also_matched": "GREGGS BAKERIES LIMITED",
    }


def test_committed_openapi_schema_matches_the_api() -> None:
    """The web app's TypeScript types are generated from this file; regenerate with
    `uv run ukco openapi && (cd ../web && npm run gen:api)` when the API changes."""
    from pathlib import Path

    committed = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "api" / "openapi.json"
    current = json.loads(json.dumps(create_app().openapi(), sort_keys=True))
    assert json.loads(committed.read_text()) == current, "API changed: regenerate the web types"


# --- Review rules (enforced by the API, not just the UI) ----------------------------------


async def test_dismissing_a_flag_or_rejecting_a_report_needs_a_reason(api) -> None:
    http, app = api
    reviews = f"/reports/{(await create_and_finish(http, app))['id']}/reviews"

    no_reason = await http.post(reviews, json={"flag_id": "F1", "decision": "dismiss"})
    assert no_reason.status_code == 422 and "needs a reason" in no_reason.text
    too_short = await http.post(
        reviews, json={"flag_id": "F1", "decision": "dismiss", "note": "ok"}
    )
    assert too_short.status_code == 422
    assert (await http.post(reviews, json={"decision": "reject", "note": " "})).status_code == 422
    confirm = await http.post(reviews, json={"flag_id": "F1", "decision": "confirm"})
    assert confirm.status_code == 201  # confirming a flag needs no justification


async def test_a_report_cannot_be_approved_with_unreviewed_flags(api) -> None:
    http, app = api
    rid = (await create_and_finish(http, app))["id"]
    reviews = f"/reports/{rid}/reviews"

    early = await http.post(reviews, json={"decision": "approve"})
    assert early.status_code == 409 and "pending: F1, F2" in early.text
    await http.post(reviews, json={"flag_id": "F1", "decision": "confirm"})
    assert (await http.post(reviews, json={"decision": "approve"})).status_code == 409
    await http.post(reviews, json={"flag_id": "F2", "decision": "confirm"})
    assert (await http.post(reviews, json={"decision": "approve"})).status_code == 201

    listed = (await http.get("/reports")).json()
    assert listed[0]["review_status"] == "approve"
    assert (await http.get(f"/reports/{rid}")).json()["review_status"] == "approve"


async def test_daily_report_limits(api, monkeypatch: pytest.MonkeyPatch) -> None:
    from ukco.config import get_settings

    http, app = api
    settings = get_settings()
    monkeypatch.setattr(settings, "reports_daily_per_user", 1)
    await create_and_finish(http, app)
    mine = await http.post("/reports", json={"company_number": ACTIVE})
    assert mine.status_code == 429 and "limit of 1 reports" in mine.json()["detail"]

    # Someone else is fine, until the site-wide limit is reached.
    other = {"X-User": "someone-else"}
    assert (
        await http.post("/reports", json={"company_number": ACTIVE}, headers=other)
    ).status_code == 202
    monkeypatch.setattr(settings, "reports_daily_total", 2)
    third = await http.post(
        "/reports", json={"company_number": ACTIVE}, headers={"X-User": "third"}
    )
    assert third.status_code == 429 and "This demo" in third.json()["detail"]
    await app.state.runner.drain()


def test_search_ranks_current_names_and_active_companies_first() -> None:
    """Companies House returns previous-name matches above the company now using the name
    (seen live for "temu"); pressing Enter must not check a company that only used to."""
    from ukco.api.app import _also_matched, rank_search_hits
    from ukco.companies_house.models import CompanySearchItem

    def item(number: str, title: str, status: str, snippet: str = "") -> CompanySearchItem:
        return CompanySearchItem(
            company_number=number, title=title, company_status=status, snippet=snippet
        )

    items = [
        item("1", "WOOLPEX LTD", "active", "TEMU LTD"),
        item("2", "15094883 LIMITED", "dissolved", "TEMU"),
        item("3", "TEMU OLD SHOP LTD", "dissolved"),
        item("4", "TEMU PAYMENTS UK LIMITED", "active"),
        item("5", "BRENEL LTD", "active", "TEMU UK LTD"),
        item("7", "TEMUJIN LTD", "active"),  # only shares the first letters
    ]
    ranked = rank_search_hits("Temu ", items)
    # current name (active, then dissolved) → previous name (active, then dissolved) → rest
    assert [i.company_number for i in ranked] == ["4", "3", "1", "5", "2", "7"]
    assert _also_matched(items[0]) == "TEMU LTD"
    assert _also_matched(items[3]) is None
    assert _also_matched(item("6", "Temu Ltd", "active", "TEMU LTD")) is None  # same name


async def test_reports_list_names_and_archive_without_deleting(api) -> None:
    http, app = api
    detail = await create_and_finish(http, app)
    rid = detail["id"]

    listed = (await http.get("/reports")).json()
    assert listed[0]["company_name"] == detail["report"]["company"]["name"]

    assert (await http.post(f"/reports/{rid}/archive")).status_code == 204
    assert rid not in [r["id"] for r in (await http.get("/reports")).json()]
    archived = (await http.get("/reports", params={"archived": True})).json()
    assert [(r["id"], r["archived_by"]) for r in archived] == [(rid, "local-dev")]
    # Archived, not deleted: the report and its audit trail are still there.
    assert (await http.get(f"/reports/{rid}")).json()["status"] == "succeeded"

    assert (await http.delete(f"/reports/{rid}/archive")).status_code == 204
    assert rid in [r["id"] for r in (await http.get("/reports")).json()]
    assert (await http.post("/reports/999999/archive")).status_code == 404
