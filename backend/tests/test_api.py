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

from tests.fakes import ACTIVE
from tests.test_report import troubled_company
from ukco.api.app import create_app
from ukco.report import store

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def api(engine: Engine) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    ch = troubled_company()
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
    assert detail["risk_score"] == 15
    report = detail["report"]
    assert report["company"]["company_number"] == ACTIVE
    assert [f["id"] for f in report["flags"]] == ["F1"]
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
    assert events[-1]["risk_score"] == 15


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
    await http.post(reviews, json={"decision": "approve"}, headers={"X-User": "lead@example.com"})

    trail = (await http.get(reviews)).json()
    assert [(r["target"], r["decision"], r["reviewer"]) for r in trail] == [
        ("F1", "confirm", "analyst@example.com"),
        ("F1", "dismiss", "analyst@example.com"),
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
    assert len((await http.get(reviews)).json()) == 3


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
