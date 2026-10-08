"""Chat persistence: conversations and their verbatim, append-only message log."""

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Engine, func, insert, select

from ukco.db.models import ChatConversation, ChatMessage
from ukco.timeutil import utc_midnight


def create_conversation(
    engine: Engine, report_id: int, user: str, *, agent_version: str, model: str
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            insert(ChatConversation)
            .values(report_id=report_id, user=user, agent_version=agent_version, model=model)
            .returning(ChatConversation.id)
        ).scalar_one()


def get_conversation(engine: Engine, conversation_id: int) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(ChatConversation.__table__).where(ChatConversation.id == conversation_id)
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def load_messages(engine: Engine, conversation_id: int) -> list[dict[str, Any]]:
    """The exact history to replay: role + content blocks, in order, unchanged."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.seq)
        ).all()
    return [{"role": r.role, "content": r.content} for r in rows]


def append(
    engine: Engine,
    conversation_id: int,
    role: str,
    content: list[Any],
    *,
    is_question: bool = False,
    usage: dict[str, Any] | None = None,
) -> int:
    with engine.begin() as conn:
        seq = conn.execute(
            select(func.coalesce(func.max(ChatMessage.seq), 0) + 1).where(
                ChatMessage.conversation_id == conversation_id
            )
        ).scalar_one()
        conn.execute(
            insert(ChatMessage).values(
                conversation_id=conversation_id,
                seq=seq,
                role=role,
                content=content,
                is_question=is_question,
                usage=usage,
            )
        )
    return seq


def questions_today(engine: Engine, user: str | None = None) -> int:
    """Questions asked since midnight UTC, by one user or by everyone (cost caps)."""
    query = (
        select(func.count())
        .select_from(ChatMessage)
        .join(ChatConversation, ChatConversation.id == ChatMessage.conversation_id)
        .where(ChatMessage.is_question, ChatMessage.created_at >= utc_midnight())
    )
    if user is not None:
        query = query.where(ChatConversation.user == user)
    with engine.connect() as conn:
        return conn.execute(query).scalar_one()


Speaker = Literal["analyst", "assistant", "tool"]


@dataclass(frozen=True)
class TranscriptEntry:
    role: Speaker
    text: str


def transcript(engine: Engine, conversation_id: int) -> list[TranscriptEntry]:
    """What a person sees: their questions, the assistant's text, and which tools it used."""
    entries: list[TranscriptEntry] = []
    with engine.connect() as conn:
        rows = conn.execute(
            select(ChatMessage.role, ChatMessage.content, ChatMessage.is_question)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.seq)
        ).all()
    for role, content, is_question in rows:
        if role == "user" and is_question:
            # The question is the message's last text block (the first message also
            # carries the report context before it). No custom fields in stored blocks:
            # they are replayed to the API verbatim.
            texts = [b["text"] for b in content if b.get("type") == "text"]
            if texts:
                entries.append(TranscriptEntry("analyst", texts[-1]))
            continue
        for block in content:
            kind = block.get("type")
            if role == "assistant" and kind == "text" and block.get("text"):
                entries.append(TranscriptEntry("assistant", block["text"]))
            elif role == "assistant" and kind == "tool_use":
                entries.append(TranscriptEntry("tool", f"{block.get('name')} {block.get('input')}"))
    return entries
