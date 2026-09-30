"""Financial facts from iXBRL accounts, and stored document bytes as evidence.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_blobs",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sha256"),
    )
    op.add_column("documents", sa.Column("format", sa.Text(), nullable=True))
    op.create_foreign_key(
        "documents_content_sha256_fkey",
        "documents",
        "document_blobs",
        ["content_sha256"],
        ["sha256"],
        ondelete="SET NULL",
    )
    op.create_table(
        "financial_facts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("company_number", sa.String(length=8), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(20, 2), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_number"], ["companies.company_number"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "metric", "period_end", name="uq_financial_facts"),
    )
    op.create_index(
        "ix_financial_facts_company_metric",
        "financial_facts",
        ["company_number", "metric", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_financial_facts_company_metric", table_name="financial_facts")
    op.drop_table("financial_facts")
    op.drop_constraint("documents_content_sha256_fkey", "documents", type_="foreignkey")
    op.drop_column("documents", "format")
    op.drop_table("document_blobs")
