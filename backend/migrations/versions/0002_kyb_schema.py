"""KYB schema: people vs appointments, PSCs, source snapshots, company deadlines.

Hand-written: autogenerate would drop the Day 3 `officers` table (which held
appointments) and lose data. Instead the old table is renamed, the new `officers`
(people) and `appointments` (edges) tables are filled from it, then it is dropped.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPOINTMENT_COLS = (
    "company_number, officer_id, role, appointed_on, resigned_on, appointment_id, name, "
    "nationality, occupation, country_of_residence, created_at, updated_at"
)


def upgrade() -> None:
    # --- companies: stubs allowed, filing deadlines, profile fetch time ---------------
    op.alter_column("companies", "fetched_at", new_column_name="profile_fetched_at")
    for col in ("name", "raw", "profile_fetched_at"):
        op.alter_column("companies", col, nullable=True)
    for name, type_ in (
        ("has_been_liquidated", sa.Boolean()),
        ("registered_office_is_in_dispute", sa.Boolean()),
        ("undeliverable_registered_office_address", sa.Boolean()),
        ("accounts_next_due", sa.Date()),
        ("accounts_overdue", sa.Boolean()),
        ("last_accounts_made_up_to", sa.Date()),
        ("confirmation_statement_next_due", sa.Date()),
        ("confirmation_statement_overdue", sa.Boolean()),
    ):
        op.add_column("companies", sa.Column(name, type_, nullable=True))

    op.add_column(
        "ingestion_runs",
        sa.Column("manifest", postgresql.JSONB(), server_default="{}", nullable=False),
    )

    # --- Day 3 `officers` held appointments: move it aside ------------------------------
    op.drop_index("ix_officers_company_number", table_name="officers")
    op.drop_index("ix_officers_officer_id", table_name="officers")
    op.rename_table("officers", "appointments_v1")

    op.create_table(
        "officers",
        sa.Column("officer_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("is_corporate", sa.Boolean(), nullable=True),
        sa.Column("total_appointments", sa.Integer(), nullable=True),
        sa.Column("appointments_truncated", sa.Boolean(), nullable=True),
        sa.Column("appointments_fetched_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("officer_id"),
    )

    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("content_key", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_key"),
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("company_number", sa.String(length=8), nullable=False),
        sa.Column("officer_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("appointed_on", sa.Date(), nullable=True),
        sa.Column("resigned_on", sa.Date(), nullable=True),
        sa.Column("appointment_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("nationality", sa.Text(), nullable=True),
        sa.Column("occupation", sa.Text(), nullable=True),
        sa.Column("country_of_residence", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["officer_id"], ["officers.officer_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("appointment_id"),
        sa.UniqueConstraint(
            "company_number",
            "officer_id",
            "role",
            "appointed_on",
            name="uq_appointments_edge",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_appointments_officer_id", "appointments", ["officer_id"], unique=False)
    op.create_table(
        "psc_statements",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("company_number", sa.String(length=8), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("notified_on", sa.Date(), nullable=True),
        sa.Column("ceased_on", sa.Date(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_number",
            "statement",
            "notified_on",
            name="uq_psc_statements",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "pscs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("company_number", sa.String(length=8), nullable=False),
        sa.Column("psc_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column(
            "natures_of_control", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("notified_on", sa.Date(), nullable=True),
        sa.Column("ceased_on", sa.Date(), nullable=True),
        sa.Column("nationality", sa.Text(), nullable=True),
        sa.Column("country_of_residence", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_number", "psc_id", name="uq_pscs_company_psc"),
    )

    # --- carry Day 3 data over (rows without an officer id can't join the network) -----
    op.execute(
        "INSERT INTO officers (officer_id, name) "
        "SELECT DISTINCT ON (officer_id) officer_id, name FROM appointments_v1 "
        "WHERE officer_id IS NOT NULL ORDER BY officer_id, updated_at DESC"
    )
    op.execute(
        f"INSERT INTO appointments ({APPOINTMENT_COLS}) "
        f"SELECT DISTINCT ON (company_number, officer_id, role, appointed_on) {APPOINTMENT_COLS} "
        "FROM appointments_v1 WHERE officer_id IS NOT NULL "
        "ORDER BY company_number, officer_id, role, appointed_on, updated_at DESC"
    )
    op.drop_table("appointments_v1")


def downgrade() -> None:
    op.create_table(
        "officers_v1",
        sa.Column("appointment_id", sa.Text(), primary_key=True),
        sa.Column(
            "company_number",
            sa.String(8),
            sa.ForeignKey("companies.company_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("officer_id", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("appointed_on", sa.Date()),
        sa.Column("resigned_on", sa.Date()),
        sa.Column("nationality", sa.Text()),
        sa.Column("occupation", sa.Text()),
        sa.Column("country_of_residence", sa.Text()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
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
    )
    op.execute(
        "INSERT INTO officers_v1 (appointment_id, company_number, officer_id, name, role, "
        "appointed_on, resigned_on, nationality, occupation, country_of_residence, fetched_at) "
        "SELECT appointment_id, company_number, officer_id, coalesce(name, ''), role, "
        "appointed_on, resigned_on, nationality, occupation, country_of_residence, updated_at "
        "FROM appointments WHERE appointment_id IS NOT NULL"
    )
    op.drop_table("pscs")
    op.drop_table("psc_statements")
    op.drop_index("ix_appointments_officer_id", table_name="appointments")
    op.drop_table("appointments")
    op.drop_table("officers")
    op.drop_table("source_snapshots")
    op.rename_table("officers_v1", "officers")
    op.create_index("ix_officers_company_number", "officers", ["company_number"])
    op.create_index("ix_officers_officer_id", "officers", ["officer_id"])

    op.drop_column("ingestion_runs", "manifest")
    for col in (
        "has_been_liquidated", "registered_office_is_in_dispute",
        "undeliverable_registered_office_address", "accounts_next_due", "accounts_overdue",
        "last_accounts_made_up_to", "confirmation_statement_next_due",
        "confirmation_statement_overdue",
    ):  # fmt: skip
        op.drop_column("companies", col)
    op.execute("DELETE FROM companies WHERE profile_fetched_at IS NULL")  # stubs
    for col in ("name", "raw", "profile_fetched_at"):
        op.alter_column("companies", col, nullable=False)
    op.alter_column("companies", "profile_fetched_at", new_column_name="fetched_at")
