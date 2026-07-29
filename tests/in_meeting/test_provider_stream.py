"""Acceptance battery for Task L2 — ``EngineProvider``: the engine's concrete SDK
provider (stream a turn).

``in_meeting.provider.EngineProvider`` drives ``claude_agent_sdk.query`` and
normalizes the native SDK message stream into ``contracts.AgentChunk`` — the
faithful port of the proven harness mapping, with the ONE deliberate difference
that options are built by the engine's ``build_engine_options`` (so the isolation
triad + the 1-hour prompt-cache directive ride EVERY call).

Deterministic and offline: ``query_fn`` is injected as an async generator over
synthetic SDK messages — no live CLI, no network, no keys. The synthetic messages
are built with the REAL ``claude_agent_sdk`` classes (they construct cleanly as
plain dataclasses against the installed SDK 0.2.128); no stand-ins were needed.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agentkit import ProviderQuery
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)
from contracts import AgentChunk

from in_meeting.provider import EngineProvider

_MODEL = "claude-opus-4-6"

#: The stable prefix (prime + map) — rides system_prompt, cached via extra_args.
_PRIME_AND_MAP = (
    "You are Proxy, joining this meeting already knowing the codebase.\n\n"
    "# Repo map (index.md)\n"
    "- services/billing/worker.py — the retry loop (backoff in _retry_call)\n"
)

_ASK = "Proxy, what's the retry logic in billing-worker?"


def _query(**overrides: Any) -> ProviderQuery:
    """A representative engine ProviderQuery (opus model, curated MCP subset)."""
    base: dict[str, Any] = {
        "model": _MODEL,
        "allowed_tools": ("mcp__code__search", "mcp__code__read"),
        "system_prompt": _PRIME_AND_MAP,
    }
    base.update(overrides)
    return ProviderQuery(**base)


def _messages() -> list[Any]:
    """A representative synthetic SDK turn: init → text + tool use → result."""
    return [
        SystemMessage(
            subtype="init",
            data={
                "session_id": "sess-1",
                "tools": ["mcp__code__search", "mcp__code__read"],
                "mcp_servers": [],
                "model": _MODEL,
            },
        ),
        AssistantMessage(
            content=[
                TextBlock(text="on it"),
                ToolUseBlock(id="tu-1", name="mcp__code__search", input={"q": "retry"}),
            ],
            model=_MODEL,
            message_id="msg-1",
            session_id="sess-1",
        ),
        ResultMessage(
            subtype="success",
            duration_ms=12,
            duration_api_ms=9,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=None,  # → RESULT metadata defaults to 0.0, never KeyErrors
            result="done",
        ),
    ]


class _FakeQueryFn:
    """An injectable ``query_fn``: records the kwargs it was invoked with and
    yields the scripted SDK messages; optionally raises mid-stream."""

    def __init__(self, messages: list[Any], *, raise_after: int | None = None) -> None:
        self._messages = messages
        self._raise_after = raise_after
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.calls.append(kwargs)
        return self._gen()

    async def _gen(self) -> AsyncIterator[Any]:
        for i, message in enumerate(self._messages):
            if self._raise_after is not None and i >= self._raise_after:
                raise RuntimeError("transport blew up mid-stream")
            yield message


class _Abort:
    """A duck-typed abort handle (the ``.aborted`` flag the provider polls)."""

    def __init__(self, aborted: bool = False) -> None:
        self.aborted = aborted


async def _drain(provider: EngineProvider, prompt: str, query: ProviderQuery) -> list[AgentChunk]:
    return [chunk async for chunk in provider.stream(prompt, query)]


# ---------------------------------------------------------------------------
# AC 1 — plumbing / mapping: SDK messages → AgentChunks, in order, exact values
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maps_sdk_messages_to_agent_chunks_in_order() -> None:
    fake = _FakeQueryFn(_messages())
    chunks = await _drain(EngineProvider(query_fn=fake), _ASK, _query())

    assert [c.type for c in chunks] == ["INIT", "TEXT", "TOOL_USE", "RESULT"]

    init = chunks[0]
    assert init.metadata["session_id"] == "sess-1"
    assert init.metadata["tools"] == ["mcp__code__search", "mcp__code__read"]

    text = chunks[1]
    assert text.text == "on it"
    assert text.metadata["msg_id"] == "msg-1"

    tool_use = chunks[2]
    assert tool_use.metadata["id"] == "tu-1"
    assert tool_use.metadata["name"] == "mcp__code__search"
    assert tool_use.metadata["input"] == {"q": "retry"}

    result = chunks[3]
    assert result.text == "done"
    assert result.metadata["session_id"] == "sess-1"
    assert result.metadata["num_turns"] == 1
    # total_cost_usd was None on the SDK message → defaults to 0.0 (never KeyErrors).
    assert result.metadata["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# AC 2 — options wired: the REAL invocation path carries the cache-ttl directive
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_fn_receives_engine_options_with_cache_ttl() -> None:
    fake = _FakeQueryFn(_messages())
    await _drain(EngineProvider(query_fn=fake), _ASK, _query())

    assert len(fake.calls) == 1
    call = fake.calls[0]
    # The volatile ask is the prompt; the options are the engine's own build.
    assert call["prompt"] == _ASK
    options = call["options"]
    assert isinstance(options, ClaudeAgentOptions)
    assert options.extra_args["system-prompt-cache-ttl"] == "1h"
    # The stable prefix rides system_prompt; the isolation triad is on the call.
    assert options.system_prompt == _PRIME_AND_MAP
    assert options.strict_mcp_config is True
    assert options.setting_sources == []


# ---------------------------------------------------------------------------
# AC 3 — graceful failure: a raising query_fn → terminal ERROR chunk, no raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transport_fault_becomes_terminal_error_chunk() -> None:
    fake = _FakeQueryFn(_messages(), raise_after=1)  # init yields, then blow up
    # Must NOT propagate the exception — never crashes the loop (SPEC §9).
    chunks = await _drain(EngineProvider(query_fn=fake), _ASK, _query())

    assert chunks, "the chunks yielded before the fault are preserved"
    assert chunks[0].type == "INIT"
    errors = [c for c in chunks if c.type == "ERROR"]
    assert len(errors) == 1
    assert chunks[-1].type == "ERROR", "the ERROR chunk is terminal"
    assert "transport blew up mid-stream" in str(chunks[-1].metadata["message"])


# ---------------------------------------------------------------------------
# AC 4 — abort halts the MODEL loop (the §3.11 hard halt), not just the result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_abort_mid_stream_halts_the_loop_early() -> None:
    full = await _drain(EngineProvider(query_fn=_FakeQueryFn(_messages())), _ASK, _query())
    assert len(full) == 4  # the un-aborted fake stream's full chunk count

    abort = _Abort()
    messages = _messages()

    async def flipping_gen(**kwargs: Any) -> AsyncIterator[Any]:
        for i, message in enumerate(messages):
            yield message
            if i == 0:
                abort.aborted = True  # fires after the first message is consumed

    chunks = await _drain(EngineProvider(query_fn=flipping_gen), _ASK, _query(abort=abort))
    # The loop BROKE on the abort poll — it did not drain the full fake stream.
    assert len(chunks) < len(full)
    assert [c.type for c in chunks] == ["INIT"]
    assert not any(c.type in ("TEXT", "RESULT") for c in chunks)


@pytest.mark.asyncio
async def test_abort_already_fired_never_starts_the_model_loop() -> None:
    fake = _FakeQueryFn(_messages())
    chunks = await _drain(
        EngineProvider(query_fn=fake), _ASK, _query(abort=_Abort(aborted=True))
    )
    assert chunks == []
    assert fake.calls == [], "query_fn is never invoked when the abort pre-fired"


# ---------------------------------------------------------------------------
# AC 5 — tripwire: a host built-in TOOL_USE in sandbox mode logs, never drops
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tripwire_logs_critical_but_still_yields_the_tool_use(
    caplog: pytest.LogCaptureFixture,
) -> None:
    messages = [
        AssistantMessage(
            content=[ToolUseBlock(id="tu-9", name="Bash", input={"command": "ls"})],
            model=_MODEL,
            message_id="msg-9",
            session_id="sess-1",
        ),
    ]
    with caplog.at_level(logging.CRITICAL):
        chunks = await _drain(
            EngineProvider(query_fn=_FakeQueryFn(messages), sandbox_mode=True),
            _ASK,
            _query(),
        )

    # The chunk is STILL yielded — the tripwire logs, it does not drop.
    tool_uses = [c for c in chunks if c.type == "TOOL_USE"]
    assert len(tool_uses) == 1
    assert tool_uses[0].metadata["name"] == "Bash"
    # The [CRITICAL] log fired as a side effect.
    assert any(
        r.levelno == logging.CRITICAL and "[CRITICAL]" in r.getMessage()
        for r in caplog.records
    )
