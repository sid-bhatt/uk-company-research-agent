"""Database schema.

Idempotency lives in the keys: every table that ingestion writes to has a natural
unique key taken from Companies House, and writes are upserts on that key. Re-running
ingestion for the same company updates rows in place instead of duplicating them.

    companies  ─┬─< officers        (appointment_id)
                └─< filings         (transaction_id) ──< documents (document_id) ──< chunks

The embedding column on `chunks` is added on Day 5, once the embedding model
(and so the vector dimension) is chosen in ADR-002.
"""

import datetime as dt
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
    __tablename__ = "companies"

    company_number: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(Text)
    date_of_creation: Mapped[dt.date | None] = mapped_column(Date)
    date_of_cessation: Mapped[dt.date | None] = mapped_column(Date)
    registered_office: Mapped[str | None] = mapped_column(Text)
    sic_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    has_insolvency_history: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict[str, Any]]
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


class Officer(Base):
    __tablename__ = "officers"
    __table_args__ = (
        Index("ix_officers_company_number", "company_number"),
        # Person id shared across companies: powers "who sits on both boards?"
        Index("ix_officers_officer_id", "officer_id"),
    )

    appointment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_number: Mapped[str] = mapped_column(
        ForeignKey("companies.company_number", ondelete="CASCADE")
    )
    officer_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    appointed_on: Mapped[dt.date | None] = mapped_column(Date)
    resigned_on: Mapped[dt.date | None] = mapped_column(Date)
    nationality: Mapped[str | None] = mapped_column(Text)
    occupation: Mapped[str | None] = mapped_column(Text)
    country_of_residence: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()


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
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
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
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = _created_at()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
