"""HTTP API: request a report, follow its progress, read it, and review it.

POST /reports                     → 202, queues a report job
GET  /reports/{id}                → status, report, current review decisions
GET  /reports/{id}/events         → server-sent progress events
GET  /reports/{id}/markdown       → the report as Markdown
GET  /reports?company_number=…    → recent reports
POST /reports/{id}/reviews        → append a decision (flag confirm/dismiss, report approve/reject)
GET  /reports/{id}/reviews        → the full, append-only audit trail
GET  /health
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import Engine, text

from ukco import __version__
from ukco.api.jobs import JobRequest, JobRunner, ProgressHub
from ukco.api.schemas import (
    CreateReportRequest,
    ReportCreated,
    ReportDetail,
    ReportSummary,
    ReviewOut,
    ReviewRequest,
)
from ukco.companies_house import CompaniesHouseClient, client_from_settings
from ukco.config import get_settings
from ukco.db import get_engine
from ukco.report import render_markdown, store
from ukco.report.narrative import NarrativeWriter
from ukco.report.pipeline import writer_from_settings
from ukco.report.schema import RiskReport


def create_app(
    *,
    engine: Engine | None = None,
    client: CompaniesHouseClient | None = None,
    writer: NarrativeWriter | None = None,
    use_settings_writer: bool = True,
) -> FastAPI:
    """Dependencies are injectable for tests; otherwise built from settings at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_client = client is None
        eng = engine or get_engine()
        ch = client or client_from_settings()
        narrator = (
            writer if writer is not None or not use_settings_writer else writer_from_settings()
        )
        store.fail_interrupted(eng)
        _install(app, eng, ch, narrator)
        yield
        await app.state.runner.drain()
        if owns_client:
            await ch.aclose()

    app = FastAPI(title="KYB Due-Diligence Agent", version=__version__, lifespan=lifespan)
    if engine is not None and client is not None:  # tests: no lifespan needed
        _install(app, engine, client, writer)
    _routes(app)
    return app


def _install(
    app: FastAPI, engine: Engine, client: CompaniesHouseClient, writer: NarrativeWriter | None
) -> None:
    app.state.engine = engine
    app.state.hub = ProgressHub()
    app.state.runner = JobRunner(engine, client, writer, app.state.hub)


def current_user(x_user: Annotated[str | None, Header()] = None) -> str:
    """Who is acting. Local dev trusts an `X-User` header; Day 12 replaces this with the
    verified identity from the frontend's session token."""
    if x_user:
        return x_user.strip()[:200]
    if get_settings().env == "local":
        return "local-dev"
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")


User = Annotated[str, Depends(current_user)]


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _report_or_404(request: Request, report_id: int) -> dict[str, Any]:
    row = store.get_report(_engine(request), report_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"report {report_id} not found")
    return row


def _routes(app: FastAPI) -> None:
    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        with _engine(request).connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    @app.post("/reports", status_code=status.HTTP_202_ACCEPTED)
    async def create_report(
        body: CreateReportRequest, request: Request, user: User
    ) -> ReportCreated:
        report_id = store.create_job(
            _engine(request),
            body.company_number,
            as_of=body.as_of,
            requested_by=user,
            params=body.model_dump(mode="json"),
        )
        request.app.state.runner.submit(
            JobRequest(report_id, body.company_number, body.as_of, body.network, body.narrative)
        )
        return ReportCreated(
            id=report_id,
            status="queued",
            links={"self": f"/reports/{report_id}", "events": f"/reports/{report_id}/events"},
        )

    @app.get("/reports")
    def list_reports(
        request: Request,
        _: User,
        company_number: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[ReportSummary]:
        rows = store.list_reports(_engine(request), company_number=company_number, limit=limit)
        return [ReportSummary.model_validate(r) for r in rows]

    @app.get("/reports/{report_id}")
    def get_report(report_id: int, request: Request, _: User) -> ReportDetail:
        row = _report_or_404(request, report_id)
        decisions = store.current_decisions(store.list_reviews(_engine(request), report_id))
        return ReportDetail(
            **ReportSummary.model_validate(row).model_dump(),
            report=RiskReport.model_validate(row["payload"]) if row["payload"] else None,
            decisions={k: ReviewOut.from_record(v) for k, v in decisions.items()},
        )

    @app.get("/reports/{report_id}/markdown", response_class=PlainTextResponse)
    def get_markdown(report_id: int, request: Request, _: User) -> str:
        row = _report_or_404(request, report_id)
        if not row["payload"]:
            raise HTTPException(status.HTTP_409_CONFLICT, f"report {report_id} is {row['status']}")
        return render_markdown(RiskReport.model_validate(row["payload"]))

    @app.get("/reports/{report_id}/events")
    async def events(report_id: int, request: Request, _: User) -> StreamingResponse:
        row = _report_or_404(request, report_id)
        hub: ProgressHub = request.app.state.hub

        async def stream() -> AsyncIterator[str]:
            if not hub.has_history(report_id) and row["status"] in store.TERMINAL:
                # Finished before this process started (or long ago): replay the outcome.
                final = {
                    "event": row["status"],
                    "report_id": report_id,
                    "error": row["error"],
                    "risk_score": row["risk_score"],
                }
                yield _sse(final)
                return
            async for message in hub.subscribe(report_id):
                yield _sse(message)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/reports/{report_id}/reviews", status_code=status.HTTP_201_CREATED)
    def add_review(report_id: int, body: ReviewRequest, request: Request, user: User) -> ReviewOut:
        try:
            record = store.add_review(
                _engine(request),
                report_id,
                flag_key=body.flag_id,
                decision=body.decision,
                note=body.note,
                reviewer=user,
            )
        except store.ReviewError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return ReviewOut.from_record(record)

    @app.get("/reports/{report_id}/reviews")
    def list_reviews(report_id: int, request: Request, _: User) -> list[ReviewOut]:
        _report_or_404(request, report_id)
        return [ReviewOut.from_record(r) for r in store.list_reviews(_engine(request), report_id)]


def _sse(message: dict[str, Any]) -> str:
    return f"event: {message['event']}\ndata: {json.dumps(message, default=str)}\n\n"
