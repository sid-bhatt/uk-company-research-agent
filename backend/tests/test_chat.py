"""Report chat: the tool loop, append-only history, guardrails, and the HTTP endpoint.

Claude is replaced by a scripted fake that streams pre-written responses and records every
request it receives, so tests can check exactly what would have been sent.
"""

import copy
import json
from collections.abc import AsyncIterator, Sequence
from itertools import pairwise
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.fakes import ACTIVE
from tests.test_report import troubled_company
from ukco.api.app import create_app
from ukco.chat.agent import (
    BINDING_BETA,
    SYSTEM_PROMPT,
    ChatError,
    ChatEvent,
    ClaudeChatAgent,
    OpenAICompatibleChatAgent,
)
from ukco.report import store
from ukco.report.narrative import FALLBACK_BETA
from ukco.report.pipeline import generate_report
from ukco.report.schema import RiskReport

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


class Block(SimpleNamespace):
    def model_dump(self, **_: Any) -> dict[str, Any]:
        return dict(self.__dict__)


def thinking() -> Block:
    return Block(type="thinking", thinking="", signature="sig-abc")


def say(t: str) -> Block:
    return Block(type="text", text=t)


def call(name: str, args: dict[str, Any], id_: str = "tu_1") -> Block:
    return Block(type="tool_use", id=id_, name=name, input=args)


class FakeStream:
    def __init__(self, blocks: list[Block], stop_reason: str) -> None:
        self.message = SimpleNamespace(
            content=blocks,
            stop_reason=stop_reason,
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=200,
                model_dump=lambda **_: {"input_tokens": 1000, "output_tokens": 200},
            ),
        )

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[Any]:
        for b in self.message.content:
            if b.type == "text":
                for word in b.text.split(" "):  # stream word by word
                    yield SimpleNamespace(type="text", text=word + " ")

    async def get_final_message(self) -> Any:
        return self.message


class ScriptedClaude:
    """Returns scripted responses in order; records each request (deep-copied)."""

    def __init__(self, *responses: tuple[list[Block], str]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs: Any) -> FakeStream:
        self.requests.append(copy.deepcopy(kwargs))
        blocks, stop = self.responses.pop(0)
        return FakeStream(blocks, stop)


@pytest.fixture
async def report(engine: Engine) -> tuple[int, RiskReport]:
    async with troubled_company().client() as client:
        r = await generate_report(client, engine, ACTIVE)
    rid = store.create_job(engine, ACTIVE, as_of=None, requested_by="analyst", params={})
    store.save_success(engine, rid, r)
    return rid, r


async def collect(events: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    return [e async for e in events]


EXPLAIN_F1 = (
    [
        thinking(),
        say("Let me check the filed accounts."),
        call("get_flag_evidence", {"flag_id": "F1"}),
    ],
    "tool_use",
)


async def test_tool_loop_answers_with_verified_citations(engine: Engine, report) -> None:
    rid, r = report
    evidence_id = r.flags[0].evidence_ids[0]
    claude = ScriptedClaude(
        EXPLAIN_F1,
        (
            [say(f"Net assets fell to £8,000 [{evidence_id}], see [F1]. Also [E999].")],
            "end_turn",
        ),
    )
    agent = ClaudeChatAgent(claude, engine)  # type: ignore[arg-type]
    events = await collect(agent.ask(rid, r, "analyst", "Why is F1 flagged?"))

    kinds = [e.type for e in events]
    assert (
        kinds[0] == "conversation" and kinds[-1] == "done" and "tool" in kinds and "text" in kinds
    )
    tool_event = next(e for e in events if e.type == "tool")
    assert tool_event.data == {"name": "get_flag_evidence", "input": {"flag_id": "F1"}}
    done = events[-1].data
    assert done["citations"] == [evidence_id, "F1"]
    assert done["unknown_citations"] == ["E999"]  # cited something that isn't in the report
    assert done["usage"] == {"input_tokens": 2000, "output_tokens": 400}

    # The tool result sent back holds the actual cited values from stored evidence.
    tool_result = claude.requests[1]["messages"][-1]["content"][0]
    values = [e["value"] for e in json.loads(tool_result["content"])["evidence"]]
    assert {"metric": "net_assets", "period_end": "2025-04-05", "value": "8000.00"} in values


async def test_history_is_append_only_and_replayed_verbatim(engine: Engine, report) -> None:
    rid, r = report
    claude = ScriptedClaude(
        EXPLAIN_F1,
        ([say("It is a fall in net assets.")], "end_turn"),
        ([say("No other flags were raised.")], "end_turn"),
    )
    agent = ClaudeChatAgent(claude, engine)  # type: ignore[arg-type]
    first = await collect(agent.ask(rid, r, "analyst", "Why is F1 flagged?"))
    conversation_id = first[0].data["conversation_id"]
    await collect(agent.ask(rid, r, "analyst", "Anything else?", conversation_id))

    histories = [req["messages"] for req in claude.requests]
    for before, after in pairwise(histories):
        assert after[: len(before)] == before  # every request extends the last one unchanged
    # The thinking block comes back exactly as produced (signature included).
    assert histories[1][1]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "sig-abc",
    }
    # The report travels once, in the first message only.
    assert histories[0][0]["content"][0]["text"].startswith("The report:")
    assert all(not b["text"].startswith("The report:") for b in histories[2][-1]["content"])
    # System prompt and tools never change within a conversation.
    assert (
        len({json.dumps([q["system"], q["tools"]], sort_keys=True) for q in claude.requests}) == 1
    )


async def test_request_shape(engine: Engine, report) -> None:
    rid, r = report
    claude = ScriptedClaude(([say("Fine.")], "end_turn"))
    await collect(ClaudeChatAgent(claude, engine).ask(rid, r, "analyst", "Hello"))  # type: ignore[arg-type]
    req = claude.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default" and set(req["betas"]) == {FALLBACK_BETA, BINDING_BETA}
    assert req["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert req["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in req  # forced tool use is a 400 on Opus 5.5
    assert all(
        t["strict"] and t["input_schema"]["additionalProperties"] is False for t in req["tools"]
    )


async def test_bad_tool_input_is_an_error_result_not_a_crash(engine: Engine, report) -> None:
    rid, r = report
    claude = ScriptedClaude(
        ([call("get_flag_evidence", {"flag_id": "F9"})], "tool_use"),
        ([say("There is no F9.")], "end_turn"),
    )
    events = await collect(ClaudeChatAgent(claude, engine).ask(rid, r, "analyst", "Explain F9"))  # type: ignore[arg-type]
    result = claude.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "no flag F9" in result["content"]
    assert events[-1].type == "done"


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ([([say("")], "refusal")], "declined"),
        ([([call("get_check", {"rule": "director_churn"})], "max_tokens")], "cut off"),
        (
            [([call("get_review_history", {}, f"tu_{i}")], "tool_use") for i in range(6)],
            "too many steps",
        ),
    ],
    ids=["refusal", "truncated-tool-call", "step-limit"],
)
async def test_guardrails_stop_the_loop(engine: Engine, report, script, message: str) -> None:
    rid, r = report
    agent = ClaudeChatAgent(ScriptedClaude(*script), engine)  # type: ignore[arg-type]
    events = await collect(agent.ask(rid, r, "analyst", "Q?"))
    assert events[-1].type == "error" and message in events[-1].data["message"]


async def test_conversations_are_private_and_tied_to_their_report(engine: Engine, report) -> None:
    rid, r = report
    agent = ClaudeChatAgent(ScriptedClaude(([say("Hi.")], "end_turn")), engine)  # type: ignore[arg-type]
    conversation_id = (await collect(agent.ask(rid, r, "analyst", "Hello")))[0].data[
        "conversation_id"
    ]

    with pytest.raises(ChatError, match="not found"):
        await collect(agent.ask(rid, r, "someone-else", "Read their chat", conversation_id))
    with pytest.raises(ChatError, match="1-1000"):
        await collect(agent.ask(rid, r, "analyst", "x" * 1001))
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE chat_messages SET content = '[]'"))


# --- HTTP endpoint ----------------------------------------------------------------------


async def test_chat_endpoint_streams_and_keeps_a_transcript(engine: Engine, report) -> None:
    rid, _ = report
    claude = ScriptedClaude(EXPLAIN_F1, ([say("Net assets fell sharply.")], "end_turn"))
    async with troubled_company().client() as ch:
        app = create_app(engine=engine, client=ch, chat_agent=ClaudeChatAgent(claude, engine))  # type: ignore[arg-type]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            ask = {"question": "Why F1?"}
            async with http.stream("POST", f"/reports/{rid}/chat", json=ask) as res:
                assert res.headers["content-type"].startswith("text/event-stream")
                body = "".join([c async for c in res.aiter_text()])
            lines = body.splitlines()
            events = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
            assert events[0]["event"] == "conversation" and events[-1]["event"] == "done"

            conv = events[0]["conversation_id"]
            turns = (await http.get(f"/reports/{rid}/chat/{conv}")).json()
            assert [t["role"] for t in turns] == ["analyst", "assistant", "tool", "assistant"]
            assert turns[0]["text"] == "Why F1?"
            other = await http.get(f"/reports/{rid}/chat/{conv}", headers={"X-User": "intruder"})
            assert other.status_code == 404


async def test_chat_endpoint_refusals(
    engine: Engine, report, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ukco.config import get_settings

    rid, _ = report
    failed = store.create_job(engine, ACTIVE, as_of=None, requested_by="x", params={})
    store.mark_failed(engine, failed, "boom")
    async with troubled_company().client() as ch:
        no_agent = create_app(engine=engine, client=ch)
        agent = ClaudeChatAgent(ScriptedClaude(), engine)  # type: ignore[arg-type]
        with_agent = create_app(engine=engine, client=ch, chat_agent=agent)

        async def ask(app: Any, report_id: int, question: str) -> int:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
                res = await http.post(f"/reports/{report_id}/chat", json={"question": question})
            return res.status_code

        assert await ask(no_agent, rid, "Q") == 503  # no LLM key configured
        assert await ask(with_agent, failed, "Q") == 409  # report didn't succeed
        assert await ask(with_agent, rid, "") == 422
        monkeypatch.setattr(get_settings(), "chat_daily_questions", 0)
        assert await ask(with_agent, rid, "Q") == 429


# --- OpenAI-compatible provider (Gemini, Groq, …) ------------------------------------------


def oai_reply(
    text: str | None = None,
    calls: Sequence[tuple[str, dict[str, Any], dict[str, Any]]] = (),
    finish: str = "stop",
) -> Any:
    tool_calls = [
        SimpleNamespace(
            id=f"call_{i}",
            type="function",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            model_extra=extra,
        )
        for i, (name, args, extra) in enumerate(calls)
    ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish,
                message=SimpleNamespace(content=text, tool_calls=tool_calls or None, refusal=None),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            model_dump=lambda **_: {"prompt_tokens": 1000, "completion_tokens": 200},
        ),
    )


class ScriptedOpenAI:
    """Chat Completions fake: scripted replies in order; records each request."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.base_url = httpx.URL("https://generativelanguage.googleapis.com/v1beta/openai/")
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.requests.append(copy.deepcopy(kwargs))
        return self.replies.pop(0)


SIGNATURE = {"extra_content": {"google": {"thought_signature": "sig-xyz"}}}


async def test_openai_compatible_tool_loop(engine: Engine, report) -> None:
    rid, r = report
    evidence_id = r.flags[0].evidence_ids[0]
    llm = ScriptedOpenAI(
        oai_reply("Checking.", [("get_flag_evidence", {"flag_id": "F1"}, SIGNATURE)], "tool_calls"),
        oai_reply(f"Net assets fell to £8,000 [{evidence_id}], see [F1]."),
        oai_reply("Nothing else was flagged."),
    )
    agent = OpenAICompatibleChatAgent(llm, engine, model="gemini-3.5-flash")  # type: ignore[arg-type]
    events = await collect(agent.ask(rid, r, "analyst", "Why is F1 flagged?"))

    assert [e.type for e in events if e.type in ("tool", "done")] == ["tool", "done"]
    done = events[-1].data
    assert done["citations"] == [evidence_id, "F1"]
    assert done["usage"] == {"input_tokens": 2000, "output_tokens": 400}
    assert done["cost_usd"] == 0  # free tier

    first, second = llm.requests[0], llm.requests[1]
    assert first["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert first["messages"][1]["content"].startswith("The report:")
    assert {t["function"]["name"] for t in first["tools"]} == {
        "get_flag_evidence",
        "get_check",
        "get_company_snapshot",
        "get_review_history",
    }
    # The tool call goes back with its provider extras (Gemini's thought signature) intact,
    # followed by its result, which holds the actual cited values.
    call, result = second["messages"][2], second["messages"][3]
    assert call["tool_calls"][0]["extra_content"] == SIGNATURE["extra_content"]
    assert result["role"] == "tool" and result["tool_call_id"] == "call_0"
    values = [e["value"] for e in json.loads(result["content"])["evidence"]]
    assert {"metric": "net_assets", "period_end": "2025-04-05", "value": "8000.00"} in values

    # Follow-up: history is replayed as an unchanged prefix, from the canonical stored form.
    await collect(agent.ask(rid, r, "analyst", "Anything else?", done["conversation_id"]))
    third = llm.requests[2]["messages"]
    assert third[: len(second["messages"]) + 1] == [
        *second["messages"],
        {"role": "assistant", "content": f"Net assets fell to £8,000 [{evidence_id}], see [F1]."},
    ]
    assert third[-1] == {"role": "user", "content": "Anything else?"}


async def test_providers_do_not_share_conversations(engine: Engine, report) -> None:
    rid, r = report
    claude_agent = ClaudeChatAgent(ScriptedClaude(([say("Hi.")], "end_turn")), engine)  # type: ignore[arg-type]
    first = await collect(claude_agent.ask(rid, r, "analyst", "Hello"))
    oai = OpenAICompatibleChatAgent(ScriptedOpenAI(), engine)  # type: ignore[arg-type]
    with pytest.raises(ChatError, match="older assistant version"):
        await collect(oai.ask(rid, r, "analyst", "Again", first[0].data["conversation_id"]))


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (oai_reply(None, finish="content_filter"), "declined"),
        (
            oai_reply(None, [("get_check", {"rule": "director_churn"}, {})], "length"),
            "cut off",
        ),
    ],
    ids=["filtered", "truncated-tool-call"],
)
async def test_openai_compatible_guardrails(
    engine: Engine, report, reply: Any, message: str
) -> None:
    rid, r = report
    agent = OpenAICompatibleChatAgent(ScriptedOpenAI(reply), engine)  # type: ignore[arg-type]
    events = await collect(agent.ask(rid, r, "analyst", "Q?"))
    assert events[-1].type == "error" and message in events[-1].data["message"]
    assert not any(e.type == "tool" for e in events)
