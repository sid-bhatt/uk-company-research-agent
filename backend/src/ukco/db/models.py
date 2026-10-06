"""Database schema.

Two layers (docs/data-model.md):
- **Register data**, upserted on Companies House natural keys so re-fetching a company
  updates rows in place, and rows are only rewritten when a tracked column changed.
- **Evidence**: every API response behind a fetch is kept in `source_snapshots`; a run's
  `manifest` lists them, so any report can be replayed from exactly what it saw.

    companies ─┬─< appointments >── officers        (person ↔ company edges: the network)
               ├─< pscs, psc_statements
               └─< filings ──< documents ──< chunks
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}  # noqa: RUF012


def _created_at() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[dt.datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Company(Base):
    """A full profile, or a **stub** (profile_fetched_at is null) for a company only
    seen through an officer's appointment list: number, name and status."""

    __tablename__ = "companies"

    company_number: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(Text)
    date_of_creation: Mapped[dt.date | None] = mapped_column(Date)
    date_of_cessation: Mapped[dt.date | None] = mapped_column(Date)
    registered_office: Mapped[str | None] = mapped_column(Text)
    sic_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    has_insolvency_history: Mapped[bool | None] = mapped_column(Boolean)
    has_been_liquidated: Mapped[bool | None] = mapped_column(Boolean)
    registered_office_is_in_dispute: Mapped[bool | None] = mapped_column(Boolean)
    undeliverable_registered_office_address: Mapped[bool | None] = mapped_column(Boolean)
    accounts_next_due: Mapped[dt.date | None] = mapped_column(Date)
    accounts_overdue: Mapped[bool | None] = mapped_column(Boolean)
    last_accounts_made_up_to: Mapped[dt.date | None] = mapped_column(Date)
    confirmation_statement_next_due: Mapped[dt.date | None] = mapped_column(Date)
    confirmation_statement_overdue: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict[str, Any] | None]
    profile_fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class Officer(Base):
    """A person (or corporate officer), identified across companies by `officer_id`."""

    __tablename__ = "officers"

    officer_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    is_corporate: Mapped[bool | None] = mapped_column(Boolean)
    total_appointments: Mapped[int | None] = mapped_column(Integer)
    appointments_truncated: Mapped[bool | None] = mapped_column(Boolean)
    appointments_fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class Appointment(Base):
    """Person ↔ company edge. Seen from the company's officer list (has appointment_id)
    and/or from the officer's appointment list (the network); both land on one row."""

    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint(
            "company_number",
            "officer_id",
            "role",
            "appointed_on",
            name="uq_appointments_edge",
            postgresql_nulls_not_distinct=True,  # pre-1992 appointments have no date
        ),
        Index("ix_appointments_officer_id", "officer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_number: Mapped[str] = mapped_column(
        ForeignKey("companies.company_number", ondelete="CASCADE")
    )
    officer_id: Mapped[str] = mapped_column(ForeignKey("officers.officer_id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)
    appointed_on: Mapped[dt.date | None] = mapped_column(Date)
    resigned_on: Mapped[dt.date | None] = mapped_column(Date)
    appointment_id: Mapped[str | None] = mapped_column(Text, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    nationality: Mapped[str | None] = mapped_column(Text)
    occupation: Mapped[str | None] = mapped_column(Text)
    country_of_residence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class Psc(Base):
    __tablename__ = "pscs"
    __table_args__ = (UniqueConstraint("company_number", "psc_id", name="uq_pscs_company_psc"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_number: Mapped[str] = mapped_column(
        ForeignKey("companies.company_number", ondelete="CASCADE")
    )
    psc_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    natures_of_control: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    notified_on: Mapped[dt.date | None] = mapped_column(Date)
    ceased_on: Mapped[dt.date | None] = mapped_column(Date)
    nationality: Mapped[str | None] = mapped_column(Text)
    country_of_residence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class PscStatement(Base):
    __tablename__ = "psc_statements"
    __table_args__ = (
        UniqueConstraint(
            "company_number",
            "statement",
            "notified_on",
            name="uq_psc_statements",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_number: Mapped[str] = mapped_column(
        ForeignKey("companies.company_number", ondelete="CASCADE")
    )
    statement: Mapped[str] = mapped_column(Text)
    notified_on: Mapped[dt.date | None] = mapped_column(Date)
    ceased_on: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class SourceSnapshot(Base):
    """One API response, stored verbatim. Identical responses share a row."""

    __tablename__ = "source_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content_key: Mapped[str] = mapped_column(String(64), unique=True)  # sha256(path, params, body)
    path: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    body: Mapped[Any] = mapped_column(JSONB)
    first_seen_at: Mapped[dt.datetime] = _created_at()
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (
        UniqueConstraint("company_number", "transaction_id", name="uq_filings_company_txn"),
        Index("ix_filings_company_date", "company_number", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_number: Mapped[str] = mapped_column(
        ForeignKey("companies.company_number", ondelete="CASCADE")
    )
    transaction_id: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    date: Mapped[dt.date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    description_values: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    pages: Mapped[int | None] = mapped_column(Integer)
    paper_filed: Mapped[bool | None] = mapped_column(Boolean)
    document_id: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


DOCUMENT_STATUSES = (
    "pending",  # known, not downloaded yet
    "downloaded",
    "parsed",  # text extracted and chunked
    "embedded",  # chunks have vectors: searchable
    "skipped_no_text",  # scanned image PDF, no text layer (OCR is a stretch goal)
    "failed",
)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in DOCUMENT_STATUSES) + ")",
            name="ck_documents_status",
        ),
        Index("ix_documents_status", "status"),
    )

    document_id: Mapped[str] = mapped_column(Text, primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id", ondelete="CASCADE"))
    company_number: Mapped[str] = mapped_column(
        ForeignKey("companies.company_number", ondelete="CASCADE")
    )
    media_type: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(Text)  # ixbrl | pdf
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    content_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("document_blobs.sha256", ondelete="SET NULL")
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class DocumentBlob(Base):
    """Downloaded document bytes, content-addressed: the evidence behind document citations.
    (Local Postgres for v1; S3 in production.)"""

    __tablename__ = "document_blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_type: Mapped[str] = mapped_column(Text)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = _created_at()


class FinancialFactRow(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint("document_id", "metric", "period_end", name="uq_financial_facts"),
        Index("ix_financial_facts_company_metric", "company_number", "metric", "period_end"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE")
    )
    company_number: Mapped[str] = mapped_column(
        ForeignKey("companies.company_number", ondelete="CASCADE")
    )
    metric: Mapped[str] = mapped_column(Text)
    period_start: Mapped[dt.date | None] = mapped_column(Date)
    period_end: Mapped[dt.date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    unit: Mapped[str | None] = mapped_column(Text)
    concept: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(Text)
    extractor_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        Index("ix_chunks_company_number", "company_number"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE")
    )
    company_number: Mapped[str] = mapped_column(String(8))  # denormalised for filtered search
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))  # skip re-embedding unchanged text
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
    created_at: Mapped[dt.datetime] = _created_at()


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trigger: Mapped[str] = mapped_column(Text)  # cli | airflow | ecs
    params: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="running")
    stats: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    # {company_number: [snapshot ids]}: replaying these reproduces the run's inputs.
    manifest: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = _created_at()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


# --- Decision data (reports, flags, reviews) --------------------------------------------

REPORT_STATUSES = ("queued", "running", "succeeded", "failed")
FLAG_DECISIONS = ("confirm", "dismiss")
REPORT_DECISIONS = ("approve", "reject")


class ReportRow(Base):
    """One requested report: a job while it runs, the validated RiskReport once done."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in REPORT_STATUSES) + ")",
            name="ck_reports_status",
        ),
        Index("ix_reports_company_created", "company_number", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_number: Mapped[str] = mapped_column(String(8))  # no FK: queued before it's fetched
    as_of: Mapped[dt.date | None] = mapped_column(Date)  # null = live
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    requested_by: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    payload: Mapped[dict[str, Any] | None]  # RiskReport JSON once succeeded
    risk_score: Mapped[int | None] = mapped_column(Integer)
    rules_version: Mapped[str | None] = mapped_column(Text)
    policy_sha256: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class FlagRow(Base):
    __tablename__ = "flags"
    __table_args__ = (UniqueConstraint("report_id", "flag_key", name="uq_flags_report_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    flag_key: Mapped[str] = mapped_column(Text)  # F1, F2, … within the report
    flag_type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_at: Mapped[dt.datetime] = _created_at()


class Review(Base):
    """Append-only audit trail (a trigger rejects UPDATE/DELETE). A flag decision has
    flag_id set; a report decision doesn't. The current decision is the latest row."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(
            "(flag_id IS NOT NULL AND decision IN ('confirm', 'dismiss')) OR "
            "(flag_id IS NULL AND decision IN ('approve', 'reject'))",
            name="ck_reviews_decision_matches_target",
        ),
        Index("ix_reviews_report_created", "report_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="RESTRICT"))
    flag_id: Mapped[int | None] = mapped_column(ForeignKey("flags.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(Text)
    # Copied from the report so each decision records exactly what the reviewer saw.
    rules_version: Mapped[str | None] = mapped_column(Text)
    policy_sha256: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()


# --- Report chat (analyst Q&A about a report) ------------------------------------------


class ChatConversation(Base):
    """One analyst's conversation about one report. The system prompt and tool set are
    fixed per conversation (`agent_version`): the model's thinking is tied to an unchanged
    prefix, so a new version starts a new conversation rather than editing an old one."""

    __tablename__ = "chat_conversations"
    __table_args__ = (Index("ix_chat_conversations_report", "report_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="RESTRICT"))
    user: Mapped[str] = mapped_column(Text)
    agent_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()


class ChatMessage(Base):
    """Every message sent to or received from the model, stored verbatim (content blocks
    as the API returned them) and replayed unchanged. Append-only, enforced by a trigger:
    it's both what preserved thinking requires and part of the audit trail."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_chat_messages_seq"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="RESTRICT")
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[list[Any]] = mapped_column(JSONB)
    is_question: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )  # the analyst typed it
    usage: Mapped[dict[str, Any] | None]
    created_at: Mapped[dt.datetime] = _created_at()
