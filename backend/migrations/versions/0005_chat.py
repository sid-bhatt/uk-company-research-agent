"""Report chat: conversations and their verbatim, append-only message log.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = """
CREATE FUNCTION chat_messages_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'chat messages are append-only: % is not allowed', TG_OP;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER chat_messages_append_only
    BEFORE UPDATE OR DELETE ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION chat_messages_append_only();
"""


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("user", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_conversations_report", "chat_conversations", ["report_id"], unique=False
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_question", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["chat_conversations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_chat_messages_seq"),
    )

    op.execute(APPEND_ONLY)


def downgrade() -> None:
    op.execute("DROP TRIGGER chat_messages_append_only ON chat_messages")
    op.execute("DROP FUNCTION chat_messages_append_only()")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_conversations_report", table_name="chat_conversations")
    op.drop_table("chat_conversations")
