"""Decision data: reports, flags, and the append-only review audit trail.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enforced in the database, not just the app: no code path can rewrite a decision.
APPEND_ONLY = """
CREATE FUNCTION reviews_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'reviews are append-only: % is not allowed', TG_OP;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER reviews_append_only
    BEFORE UPDATE OR DELETE ON reviews
    FOR EACH ROW EXECUTE FUNCTION reviews_append_only();
"""


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("company_number", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=True),
        sa.Column("policy_sha256", sa.String(length=64), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_reports_status"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reports_company_created", "reports", ["company_number", "created_at"], unique=False
    )
    op.create_table(
        "flags",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("flag_key", sa.Text(), nullable=False),
        sa.Column("flag_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("evidence_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "flag_key", name="uq_flags_report_key"),
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("flag_id", sa.BigInteger(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=True),
        sa.Column("policy_sha256", sa.String(length=64), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(flag_id IS NOT NULL AND decision IN ('confirm', 'dismiss')) OR "
            "(flag_id IS NULL AND decision IN ('approve', 'reject'))",
            name="ck_reviews_decision_matches_target",
        ),
        sa.ForeignKeyConstraint(["flag_id"], ["flags.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reviews_report_created", "reviews", ["report_id", "created_at"], unique=False
    )
    op.execute(APPEND_ONLY)


def downgrade() -> None:
    op.execute("DROP TRIGGER reviews_append_only ON reviews")
    op.execute("DROP FUNCTION reviews_append_only()")
    op.drop_index("ix_reviews_report_created", table_name="reviews")
    op.drop_table("reviews")
    op.drop_table("flags")
    op.drop_index("ix_reports_company_created", table_name="reports")
    op.drop_table("reports")
