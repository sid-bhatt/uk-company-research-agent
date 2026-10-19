"""Persistence for report jobs, their flags, and the append-only review trail."""

import datetime as dt
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Engine, Table, func, insert, select, update

from ukco.db.models import FlagRow, ReportRow, Review
from ukco.report.schema import RiskReport
from ukco.timeutil import utc_midnight

TERMINAL = ("succeeded", "failed")
MIN_REASON_CHARS = 10


class ReviewError(ValueError):
    """A review that can't be recorded. `status` is the HTTP status the API should use."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def create_job(
    engine: Engine,
    company_number: str,
    *,
    as_of: dt.date | None,
    requested_by: str,
    params: dict[str, Any],
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            insert(ReportRow)
            .values(
                company_number=company_number, as_of=as_of, requested_by=requested_by, params=params
            )
            .returning(ReportRow.id)
        ).scalar_one()


def reports_today(engine: Engine, user: str | None = None) -> int:
    """Reports requested since midnight UTC, by one user or by everyone."""
    query = (
        select(func.count()).select_from(ReportRow).where(ReportRow.created_at >= utc_midnight())
    )
    if user is not None:
        query = query.where(ReportRow.requested_by == user)
    with engine.connect() as conn:
        return conn.execute(query).scalar_one()


def mark_running(engine: Engine, report_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(ReportRow)
            .where(ReportRow.id == report_id)
            .values(status="running", started_at=func.now())
        )


def save_success(engine: Engine, report_id: int, report: RiskReport) -> None:
    llm = report.provenance.llm
    with engine.begin() as conn:
        conn.execute(
            update(ReportRow)
            .where(ReportRow.id == report_id)
            .values(
                status="succeeded",
                payload=report.model_dump(mode="json"),
                risk_score=report.risk_score,
                as_of=report.as_of,
                rules_version=report.provenance.rules_version,
                policy_sha256=report.provenance.policy_sha256,
                model_id=llm.served_by or llm.model if llm else None,
                prompt_version=llm.prompt_version if llm else None,
                cost_usd=llm.cost_usd if llm else None,
                latency_ms=llm.latency_ms if llm else None,
                finished_at=func.now(),
            )
        )
        if report.flags:
            conn.execute(
                insert(FlagRow),
                [
                    {
                        "report_id": report_id,
                        "flag_key": f.id,
                        "flag_type": f.flag_type,
                        "severity": f.severity,
                        "points": f.points,
                        "title": f.title,
                        "explanation": f.explanation,
                        "evidence_ids": f.evidence_ids,
                    }
                    for f in report.flags
                ],
            )


def mark_failed(engine: Engine, report_id: int, error: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(ReportRow)
            .where(ReportRow.id == report_id)
            .values(status="failed", error=error[:4000], finished_at=func.now())
        )


def fail_interrupted(engine: Engine) -> int:
    """Jobs run in-process; any left queued/running at startup died with the last process."""
    with engine.begin() as conn:
        return conn.execute(
            update(ReportRow)
            .where(ReportRow.status.in_(("queued", "running")))
            .values(
                status="failed",
                error="interrupted by a restart; request it again",
                finished_at=func.now(),
            )
        ).rowcount


def get_report(engine: Engine, report_id: int) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = (
            conn.execute(select(ReportRow.__table__).where(ReportRow.id == report_id))
            .mappings()
            .first()
        )
    return dict(row) if row else None


def list_reports(
    engine: Engine, *, company_number: str | None, limit: int, archived: bool = False
) -> list[dict[str, Any]]:
    """Recent reports without payloads, each with the company name and its current
    report-level decision. Archived reports are listed only when asked for."""
    table = cast(Table, ReportRow.__table__)
    latest_decision = (
        select(Review.decision)
        .where(Review.report_id == ReportRow.id, Review.flag_id.is_(None))
        .order_by(Review.created_at.desc(), Review.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    stmt = select(
        *(c for c in table.columns if c.name != "payload"),
        ReportRow.payload["company"]["name"].astext.label("company_name"),
        latest_decision.label("review_status"),
    ).where(ReportRow.archived_at.is_not(None) if archived else ReportRow.archived_at.is_(None))
    if company_number:
        stmt = stmt.where(ReportRow.company_number == company_number)
    with engine.connect() as conn:
        rows = (
            conn.execute(stmt.order_by(ReportRow.created_at.desc()).limit(limit)).mappings().all()
        )
    return [dict(r) for r in rows]


def set_archived(engine: Engine, report_id: int, user: str | None) -> bool:
    """Archive (user given) or restore (None). False if the report doesn't exist."""
    values = (
        {"archived_at": func.now(), "archived_by": user}
        if user
        else {"archived_at": None, "archived_by": None}
    )
    with engine.begin() as conn:
        result = conn.execute(
            update(ReportRow)
            .where(ReportRow.id == report_id)
            .values(**values)
            .returning(ReportRow.id)
        )
        return result.first() is not None


@dataclass(frozen=True)
class ReviewRecord:
    id: int
    target: str  # "report" or a flag key such as "F1"
    decision: str
    note: str | None
    reviewer: str
    created_at: dt.datetime
    rules_version: str | None
    model_id: str | None


def add_review(
    engine: Engine,
    report_id: int,
    *,
    flag_key: str | None,
    decision: str,
    note: str | None,
    reviewer: str,
) -> ReviewRecord:
    report = get_report(engine, report_id)
    if report is None:
        raise ReviewError(404, f"report {report_id} not found")
    if report["status"] != "succeeded":
        raise ReviewError(
            409, f"report {report_id} is {report['status']}; only finished reports can be reviewed"
        )

    flag_id = None
    if flag_key is not None:
        if decision not in ("confirm", "dismiss"):
            raise ReviewError(422, "a flag can be confirmed or dismissed")
        with engine.connect() as conn:
            flag_id = conn.execute(
                select(FlagRow.id).where(
                    FlagRow.report_id == report_id, FlagRow.flag_key == flag_key
                )
            ).scalar_one_or_none()
        if flag_id is None:
            raise ReviewError(404, f"report {report_id} has no flag {flag_key}")
    elif decision not in ("approve", "reject"):
        raise ReviewError(422, "a report can be approved or rejected")

    # Compliance rules, enforced here rather than trusted to the UI:
    if decision in ("dismiss", "reject") and len((note or "").strip()) < MIN_REASON_CHARS:
        target = "dismissing a flag" if flag_key else "rejecting a report"
        raise ReviewError(422, f"{target} needs a reason (at least {MIN_REASON_CHARS} characters)")
    if decision == "approve":
        decided = current_decisions(list_reviews(engine, report_id))
        with engine.connect() as conn:
            keys = (
                conn.execute(select(FlagRow.flag_key).where(FlagRow.report_id == report_id))
                .scalars()
                .all()
            )
        pending = sorted(k for k in keys if k not in decided)
        if pending:
            raise ReviewError(
                409, f"review every flag before approving; pending: {', '.join(pending)}"
            )

    with engine.begin() as conn:
        row = conn.execute(
            insert(Review)
            .values(
                report_id=report_id,
                flag_id=flag_id,
                decision=decision,
                note=note,
                reviewer=reviewer,
                rules_version=report["rules_version"],
                policy_sha256=report["policy_sha256"],
                model_id=report["model_id"],
                prompt_version=report["prompt_version"],
            )
            .returning(Review.id, Review.created_at)
        ).one()
    return ReviewRecord(
        row.id,
        flag_key or "report",
        decision,
        note,
        reviewer,
        row.created_at,
        report["rules_version"],
        report["model_id"],
    )


def list_reviews(engine: Engine, report_id: int) -> list[ReviewRecord]:
    """The full audit trail, oldest first."""
    stmt = (
        select(
            Review.id,
            FlagRow.flag_key,
            Review.decision,
            Review.note,
            Review.reviewer,
            Review.created_at,
            Review.rules_version,
            Review.model_id,
        )
        .outerjoin(FlagRow, FlagRow.id == Review.flag_id)
        .where(Review.report_id == report_id)
        .order_by(Review.created_at, Review.id)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    return [
        ReviewRecord(
            r.id,
            r.flag_key or "report",
            r.decision,
            r.note,
            r.reviewer,
            r.created_at,
            r.rules_version,
            r.model_id,
        )
        for r in rows
    ]


def current_decisions(reviews: list[ReviewRecord]) -> dict[str, ReviewRecord]:
    """Latest decision per target ("report", "F1", …): the trail is never rewritten."""
    latest: dict[str, ReviewRecord] = {}
    for r in reviews:
        latest[r.target] = r
    return latest
