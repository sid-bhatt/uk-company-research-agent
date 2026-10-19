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
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anthropic
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import Engine, text

from ukco import __version__
from ukco.api.jobs import JobRequest, JobRunner, ProgressHub
from ukco.api.schemas import (
    ChatRequest,
    ChatTurn,
    CompanyHit,
    CreateReportRequest,
    ReportCreated,
    ReportDetail,
    ReportSummary,
    ReviewOut,
    ReviewRequest,
)
from ukco.chat import store as chat_store
from ukco.chat.agent import ChatAgent, ChatError
from ukco.companies_house import CompaniesHouseClient, CompaniesHouseError, client_from_settings
from ukco.companies_house.models import CompanySearchItem
from ukco.config import check_production, get_settings
from ukco.db import get_engine
from ukco.observability import setup_tracing, shutdown_tracing
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
    chat_agent: ChatAgent | None = None,
) -> FastAPI:
    """Dependencies are injectable for tests; otherwise built from settings at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        check_production(get_settings())
        owns_client = client is None
        eng = engine or get_engine()
        ch = client or client_from_settings()
        narrator = (
            writer if writer is not None or not use_settings_writer else writer_from_settings()
        )
        setup_tracing("ukco-api")
        store.fail_interrupted(eng)
        _install(app, eng, ch, narrator, chat_agent or chat_agent_from_settings(eng))
        yield
        await app.state.runner.drain()
        if owns_client:
            await ch.aclose()
        shutdown_tracing()

    app = FastAPI(title="KYB Due-Diligence Agent", version=__version__, lifespan=lifespan)
    if engine is not None and client is not None:  # tests: no lifespan needed
        _install(app, engine, client, writer, chat_agent)
    _routes(app)
    return app


def _install(
    app: FastAPI,
    engine: Engine,
    client: CompaniesHouseClient,
    writer: NarrativeWriter | None,
    chat_agent: "ChatAgent | None" = None,
) -> None:
    app.state.engine = engine
    app.state.client = client
    app.state.hub = ProgressHub()
    app.state.runner = JobRunner(engine, client, writer, app.state.hub)
    app.state.chat_agent = chat_agent


def chat_agent_from_settings(engine: Engine) -> "ChatAgent | None":
    settings = get_settings()
    key = settings.anthropic_api_key
    if not key or not key.get_secret_value().strip():
        return None
    client = anthropic.AsyncAnthropic(api_key=key.get_secret_value().strip())
    return ChatAgent(client, engine, model=settings.llm_model, effort=settings.chat_effort)


JWT_ISSUER, JWT_AUDIENCE = "ukco-web", "ukco-api"


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    x_user: Annotated[str | None, Header()] = None,
) -> str:
    """Who is acting. With API_JWT_SECRET set, a valid token from the web app is required
    (the web app authenticates the person, then signs a short-lived token naming them).
    Without it, only local development trusts an `X-User` header."""
    settings = get_settings()
    secret = settings.api_jwt_secret
    if secret is not None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token required")
        try:
            claims = jwt.decode(
                token,
                secret.get_secret_value(),
                algorithms=["HS256"],
                audience=JWT_AUDIENCE,
                issuer=JWT_ISSUER,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
        return str(claims["sub"])[:200]
    if settings.env == "local":
        return (x_user or "local-dev").strip()[:200]
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")


User = Annotated[str, Depends(current_user)]


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _report_or_404(request: Request, report_id: int) -> dict[str, Any]:
    row = store.get_report(_engine(request), report_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"report {report_id} not found")
    return row


def _within_limit(what: str, mine: int, per_user: int, everyone: int, total: int) -> None:
    """429 once today's usage reaches the per-user or the site-wide daily limit."""
    if mine >= per_user:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've reached today's limit of {per_user} {what}. It resets at midnight UTC.",
        )
    if everyone >= total:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"This demo has reached today's limit of {total} {what}. Try again tomorrow.",
        )


def _also_matched(item: CompanySearchItem) -> str | None:
    snippet = (item.snippet or "").strip()
    return snippet if snippet and snippet.casefold() != item.title.casefold() else None


def rank_search_hits(query: str, items: list[CompanySearchItem]) -> list[CompanySearchItem]:
    """Whole-word matches on the current name first, then on another matched name (usually
    a previous one), then the rest; active companies before others within each group, and
    Companies House's own order breaks ties. Without this, a company that merely used to
    carry a well-known name can outrank the one that carries it now."""
    wanted = set(re.findall(r"\w+", query.casefold()))

    def has_all(text: str | None) -> bool:
        return wanted <= set(re.findall(r"\w+", (text or "").casefold()))

    def key(item: CompanySearchItem) -> tuple[int, bool]:
        tier = 0 if has_all(item.title) else 1 if has_all(item.snippet) else 2
        return (tier, item.company_status != "active")

    return sorted(items, key=key)


def _routes(app: FastAPI) -> None:
    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        with _engine(request).connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    @app.get("/companies/search")
    async def search_companies(
        request: Request,
        _: User,
        q: Annotated[str, Query(min_length=1, max_length=160)],
        limit: Annotated[int, Query(ge=1, le=20)] = 8,
    ) -> list[CompanyHit]:
        client: CompaniesHouseClient = request.app.state.client
        try:
            # Ask for more than we show: Companies House ranks previous-name matches
            # alongside (often above) current-name ones, so we re-rank before cutting.
            result = await client.search_companies(q, limit=min(limit * 3, 30))
        except CompaniesHouseError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Companies House: {exc}") from exc
        return [
            CompanyHit(
                company_number=c.company_number,
                name=c.title,
                status=c.company_status,
                incorporated=c.date_of_creation,
                address=c.address_snippet,
                also_matched=_also_matched(c),
            )
            for c in rank_search_hits(q, result.items)[:limit]
        ]

    @app.post("/reports", status_code=status.HTTP_202_ACCEPTED)
    async def create_report(
        body: CreateReportRequest, request: Request, user: User
    ) -> ReportCreated:
        settings = get_settings()
        _within_limit(
            "reports",
            store.reports_today(_engine(request), user),
            settings.reports_daily_per_user,
            store.reports_today(_engine(request)),
            settings.reports_daily_total,
        )
        report_id = store.create_job(
            _engine(request),
            body.company_number,
            as_of=body.as_of,
            requested_by=user,
            params=body.model_dump(mode="json"),
        )
        request.app.state.runner.submit(
            JobRequest(
                report_id, body.company_number, body.as_of, body.network, body.narrative, user
            )
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
        archived: bool = False,
    ) -> list[ReportSummary]:
        rows = store.list_reports(
            _engine(request), company_number=company_number, limit=limit, archived=archived
        )
        return [ReportSummary.model_validate(r) for r in rows]

    @app.post("/reports/{report_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
    def archive_report(report_id: int, request: Request, user: User) -> None:
        """Hide a report from lists. Nothing is deleted: reports and their reviews are the
        audit record, so archiving is recorded (who, when) and can be undone."""
        if not store.set_archived(_engine(request), report_id, user):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"report {report_id} not found")

    @app.delete("/reports/{report_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
    def restore_report(report_id: int, request: Request, _: User) -> None:
        if not store.set_archived(_engine(request), report_id, None):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"report {report_id} not found")

    @app.get("/reports/{report_id}")
    def get_report(report_id: int, request: Request, _: User) -> ReportDetail:
        row = _report_or_404(request, report_id)
        decisions = store.current_decisions(store.list_reviews(_engine(request), report_id))
        row["review_status"] = decisions["report"].decision if "report" in decisions else None
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

    @app.post("/reports/{report_id}/chat")
    async def chat(
        report_id: int, body: ChatRequest, request: Request, user: User
    ) -> StreamingResponse:
        """Ask about a report. Streams server-sent events: conversation, tool, text, done/error."""
        agent: ChatAgent | None = request.app.state.chat_agent
        if agent is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "chat needs ANTHROPIC_API_KEY")
        row = _report_or_404(request, report_id)
        if row["status"] != "succeeded" or not row["payload"]:
            raise HTTPException(status.HTTP_409_CONFLICT, f"report {report_id} is {row['status']}")
        settings = get_settings()
        _within_limit(
            "questions",
            chat_store.questions_today(_engine(request), user),
            settings.chat_daily_questions,
            chat_store.questions_today(_engine(request)),
            settings.chat_daily_total,
        )

        events = agent.ask(
            report_id,
            RiskReport.model_validate(row["payload"]),
            user,
            body.question,
            body.conversation_id,
        )
        try:  # surface bad input as an HTTP error before the stream starts
            first = await anext(events)
        except ChatError as exc:
            code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc)
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(code, str(exc)) from exc

        async def stream() -> AsyncIterator[str]:
            yield _sse({"event": first.type, **first.data})
            async for event in events:
                yield _sse({"event": event.type, **event.data})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/reports/{report_id}/chat/{conversation_id}")
    def chat_transcript(
        report_id: int, conversation_id: int, request: Request, user: User
    ) -> list[ChatTurn]:
        conv = chat_store.get_conversation(_engine(request), conversation_id)
        if conv is None or conv["report_id"] != report_id or conv["user"] != user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        return [
            ChatTurn(role=t.role, text=t.text)
            for t in chat_store.transcript(_engine(request), conversation_id)
        ]

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
