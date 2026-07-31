"""Acceptance battery for Task L8 — the engine loop / wake handoff (the integration spine).

``in_meeting.engine.Engine`` is the always-on loop that composes the merged
pieces (M1 notes + M2 trigger + L1 context + L5 prompt + L2 provider): transcript
lines accumulate as notes and feed the trigger; when the trigger fires, Proxy
wakes with the FULL context (prime + map cached prefix, recent notes + ask
volatile prompt), runs ONE streamed provider turn, routes spoken TEXT to the
``speak`` sink, and returns to listening. "Claude Code, pointed at a meeting."

Deterministic and offline: the provider is a scripted fake ``agentkit.Provider``
(no live CLI, no network). Wake turns run as BACKGROUND tasks since
ENGINE-CONCURRENCY (L7/W2 — see ``test_engine_concurrency.py``), so each test
``await``s ``engine.drain()`` after feeding before reading a turn's outcome; every
assertion below is unchanged in strength. The six AC groups (SPEC §3/§4/§9):

1. wake with full context — captured provider args carry prime + map + injection
   guardrail in ``system_prompt`` and the ask + recent notes in ``prompt``;
2. spoken output routed — TEXT reaches the sink; the ``TurnResult`` carries it;
3. return to the loop + idle=free — an idle line after a turn returns ``None``
   with an EXACT provider call-count of one (and zero before any wake);
4. graceful failure — an ERROR chunk AND a mid-stream raise are surfaced
   honestly in the result while the loop SURVIVES for the next addressed line;
5. no-map — ``map_text=None`` runs a valid turn (prime + guardrail, no map block);
6. worker/chat paths wake and run a turn the same way.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest
from agentkit import ProviderQuery, injection_guardrail_suffix
from agentkit.execution import INJECTION_GUARDRAIL_MARK
from contracts import AgentChunk

from in_meeting.engine import CODE_TOOLS, Engine, TurnResult
from in_meeting.notes import TranscriptLine
from in_meeting.prompt import PROXY_SYSTEM_PROMPT
from in_meeting.trigger import ChatLine

_MODEL = "claude-opus-4-6"
_TOOLS: tuple[str, ...] = ("mcp__code__search", "mcp__code__read")
_MAP = "# Map\n- auth in services/auth/login.py\n- retries in libs/http/client.py"
_ASK = "Proxy, where's the retry logic?"
_ANSWER = "on it, the retry logic is in client.py:42"

_IDLE_LINES: list[tuple[str, str, float]] = [
    ("Priya", "Let's look at the flaky checkout calls.", 10.2),
    ("Marcus", "They fail once then succeed on retry.", 14.8),
]


def _line(text: str, speaker: str = "Devon", timestamp: float = 20.0) -> TranscriptLine:
    return TranscriptLine(text=text, speaker=speaker, timestamp=timestamp, end_of_turn=True)


async def _confirm_every_hit(text: str) -> bool:
    """The injected ASYNC disambiguation seam, scripted to confirm every name-hit."""
    return True


def _happy_turn() -> list[AgentChunk]:
    """The brief's scripted turn: INIT → TEXT (the spoken answer) → RESULT."""
    return [
        AgentChunk(type="INIT", text=None, metadata={"session_id": "sess-1", "tools": [], "mcp_servers": []}),
        AgentChunk(type="TEXT", text=_ANSWER, metadata={"msg_id": "m-1"}),
        AgentChunk(type="RESULT", text=_ANSWER, metadata={"session_id": "sess-1", "total_cost_usd": 0.01}),
    ]


class FakeProvider:
    """A scripted ``agentkit.Provider``: records every ``(prompt, query)`` call and
    replays the per-call chunk script (the last script repeats for extra calls)."""

    def __init__(self, turns: Sequence[Sequence[AgentChunk]] | None = None) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []
        self._turns: list[list[AgentChunk]] = [list(t) for t in (turns or [_happy_turn()])]

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        script = self._turns[min(len(self.calls) - 1, len(self._turns) - 1)]
        for chunk in script:
            yield chunk


class FlakyProvider:
    """Raises MID-STREAM on the first call (partial TEXT already out), then replays
    the happy turn — the loop-survival half of AC4."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        if len(self.calls) == 1:
            yield AgentChunk(type="TEXT", text="checking now", metadata={"msg_id": "m-err"})
            raise RuntimeError("SDK subprocess died mid-turn")
        for chunk in _happy_turn():
            yield chunk


def _engine(
    provider: FakeProvider | FlakyProvider,
    *,
    map_text: str | None = _MAP,
) -> tuple[Engine, list[str]]:
    """An Engine wired to a list-capturing async speak sink; disambiguate confirms
    every name-hit (the injected judgment seam, scripted for determinism)."""
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    engine = Engine(
        provider=provider,
        model=_MODEL,
        allowed_tools=_TOOLS,
        speak=speak,
        disambiguate=_confirm_every_hit,
        map_text=map_text,
    )
    return engine, spoken


async def _feed_idle_context(engine: Engine, provider: FakeProvider | FlakyProvider) -> None:
    """Accumulate idle notes first — none of them may touch the provider."""
    for speaker, text, t in _IDLE_LINES:
        assert await engine.feed_transcript(_line(text, speaker, t)) is None
    assert provider.calls == [], "an idle line did provider work (idle must be free)"


# ── AC1: wake with FULL context ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_addressed_line_wakes_with_full_context() -> None:
    """AC1 — the provider is called with prime+map+guardrail in system_prompt and
    the ask + recent notes in the volatile prompt (captured args, not inference)."""
    provider = FakeProvider()
    engine, _ = _engine(provider)
    await _feed_idle_context(engine, provider)

    engagement = await engine.feed_transcript(_line(_ASK))
    await engine.drain()

    assert engagement is not None and engagement.source == "voice"
    assert len(provider.calls) == 1
    prompt, query = provider.calls[0]
    # Stable cached prefix: prime + map + the injection guardrail as the final word.
    assert PROXY_SYSTEM_PROMPT in query.system_prompt
    assert _MAP in query.system_prompt
    assert query.system_prompt.rstrip().endswith(injection_guardrail_suffix())
    # Volatile prompt: the ask + the recent notes tail (and never in the prefix).
    assert _ASK in prompt
    for speaker, text, _ in _IDLE_LINES:
        assert text in prompt and speaker in prompt
    assert _ASK not in query.system_prompt
    # Model + curated tools thread through unchanged.
    assert query.model == _MODEL
    assert query.allowed_tools == _TOOLS


# ── AC2: spoken output routed to the sink ─────────────────────────────────────


@pytest.mark.asyncio
async def test_spoken_text_reaches_the_sink_and_the_turn_result() -> None:
    """AC2 — the TEXT chunk reaches ``speak``; the TurnResult carries the spoken text."""
    provider = FakeProvider()
    engine, spoken = _engine(provider)

    await engine.feed_transcript(_line(_ASK))
    await engine.drain()

    assert spoken == [_ANSWER]
    turn = engine.last_turn
    assert isinstance(turn, TurnResult)
    assert turn.spoken == _ANSWER
    assert turn.source == "voice"
    assert turn.error is None


@pytest.mark.asyncio
async def test_accumulated_text_chunks_speak_only_the_new_suffix() -> None:
    """AC2 (streaming physics) — the real provider's TEXT chunks carry ACCUMULATED
    text per msg_id (provider.py contract); the Engine speaks only each new suffix,
    never the same words twice."""
    provider = FakeProvider(
        turns=[
            [
                AgentChunk(type="TEXT", text="on it", metadata={"msg_id": "m-1"}),
                AgentChunk(type="TEXT", text=_ANSWER, metadata={"msg_id": "m-1"}),
                AgentChunk(type="RESULT", text=_ANSWER, metadata={}),
            ]
        ]
    )
    engine, spoken = _engine(provider)

    await engine.feed_transcript(_line(_ASK))
    await engine.drain()

    assert spoken == ["on it", ", the retry logic is in client.py:42"]
    assert engine.last_turn is not None
    assert engine.last_turn.spoken == _ANSWER


# ── AC3: return to the loop; idle = free ──────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_resumes_and_idle_stays_free_after_a_turn() -> None:
    """AC3 — after a turn, an idle line returns None and the provider call count is
    EXACTLY one across an addressed line + an idle line."""
    provider = FakeProvider()
    engine, _ = _engine(provider)

    assert await engine.feed_transcript(_line("No wake word here at all.")) is None
    assert len(provider.calls) == 0  # idle before any wake: zero provider work

    assert await engine.feed_transcript(_line(_ASK)) is not None
    assert await engine.feed_transcript(_line("Moving on to the roadmap.")) is None
    await engine.drain()
    assert len(provider.calls) == 1  # exactly ONE turn ran across both lines


# ── AC4: graceful failure — the loop never crashes ────────────────────────────


@pytest.mark.asyncio
async def test_error_chunk_is_surfaced_honestly_and_the_loop_survives() -> None:
    """AC4a — an ERROR chunk becomes an honest TurnResult.error; the NEXT addressed
    line still runs a full turn."""
    error_turn = [
        AgentChunk(type="INIT", text=None, metadata={"session_id": "s", "tools": [], "mcp_servers": []}),
        AgentChunk(type="ERROR", text=None, metadata={"message": "auth expired: run /login"}),
    ]
    provider = FakeProvider(turns=[error_turn, _happy_turn()])
    engine, spoken = _engine(provider)

    first = await engine.feed_transcript(_line(_ASK))
    await engine.drain()
    assert first is not None
    assert engine.last_turn is not None
    assert engine.last_turn.error is not None and "auth expired" in engine.last_turn.error

    second = await engine.feed_transcript(_line("Proxy, try that lookup again?"))
    await engine.drain()
    assert second is not None
    assert len(provider.calls) == 2  # the loop survived and ran the next turn
    assert engine.last_turn.error is None
    assert spoken[-1] == _ANSWER


@pytest.mark.asyncio
async def test_raising_provider_is_surfaced_honestly_and_the_loop_survives() -> None:
    """AC4b — a provider that RAISES mid-stream never crashes the Engine: the
    partial speech stands, the error is honest, and the next turn runs."""
    provider = FlakyProvider()
    engine, spoken = _engine(provider)

    first = await engine.feed_transcript(_line(_ASK))
    await engine.drain()
    assert first is not None  # feed_transcript did not raise
    assert engine.last_turn is not None
    assert engine.last_turn.error is not None and "died mid-turn" in engine.last_turn.error
    assert spoken == ["checking now"]  # the partial pre-fault speech was delivered

    second = await engine.feed_transcript(_line("Proxy, are you still with us?"))
    await engine.drain()
    assert second is not None
    assert len(provider.calls) == 2
    assert engine.last_turn.error is None
    assert engine.last_turn.spoken == _ANSWER


# ── AC5: no-map degradation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_map_runs_a_valid_turn_with_prime_and_guardrail_only() -> None:
    """AC5 — map_text=None (unindexed repo): the turn runs; the prefix has the
    prime + guardrail but no map block."""
    provider = FakeProvider()
    engine, spoken = _engine(provider, map_text=None)

    engagement = await engine.feed_transcript(_line(_ASK))
    await engine.drain()

    assert engagement is not None
    assert engine.last_turn is not None and engine.last_turn.error is None
    assert spoken == [_ANSWER]
    _, query = provider.calls[0]
    assert PROXY_SYSTEM_PROMPT in query.system_prompt
    assert INJECTION_GUARDRAIL_MARK in query.system_prompt
    assert "# Repository map" not in query.system_prompt


# ── AC6: worker + chat paths wake the same way ────────────────────────────────


@pytest.mark.asyncio
async def test_worker_done_wakes_a_turn_carrying_the_result() -> None:
    """AC6a — a finished worker wakes a full-context turn; its id + result reach
    the volatile prompt verbatim (the payload is carried, never re-parsed)."""
    provider = FakeProvider()
    engine, spoken = _engine(provider)
    await _feed_idle_context(engine, provider)

    engagement = await engine.on_worker_done("w-7", "build green: 128 tests passed")
    await engine.drain()

    assert engagement.source == "worker"
    assert len(provider.calls) == 1
    prompt, query = provider.calls[0]
    assert "w-7" in prompt
    assert "build green: 128 tests passed" in prompt
    assert PROXY_SYSTEM_PROMPT in query.system_prompt
    assert spoken == [_ANSWER]
    assert engine.last_turn is not None and engine.last_turn.source == "worker"


@pytest.mark.asyncio
async def test_chat_at_proxy_wakes_a_turn_and_plain_chat_is_free() -> None:
    """AC6b — an @proxy chat message wakes a turn; a plain chat line does zero
    provider work. The object-with-``say`` speak shape is accepted too."""
    provider = FakeProvider()
    captured: list[str] = []

    class Sink:
        async def say(self, text: str) -> None:
            captured.append(text)

    engine = Engine(
        provider=provider,
        model=_MODEL,
        allowed_tools=_TOOLS,
        speak=Sink(),
        disambiguate=_confirm_every_hit,
    )

    assert await engine.feed_chat(ChatLine(sender="Priya", message="the proxy server is fine")) is None
    assert provider.calls == []

    engagement = await engine.feed_chat(ChatLine(sender="Priya", message="@proxy summarize the decision"))
    await engine.drain()
    assert engagement is not None and engagement.source == "chat"
    assert len(provider.calls) == 1
    prompt, _ = provider.calls[0]
    assert "@proxy summarize the decision" in prompt
    assert captured == [_ANSWER]


# ── CODE-LOOKUP: the grounding toolbelt reaches the real invocation path ──────


def test_code_tools_names_the_four_canonical_code_intel_tools() -> None:
    """CODE-LOOKUP AC3 — CODE_TOOLS is the engine-local constant callers pass as
    allowed_tools; it names EXACTLY the premeeting toolbelt's four tools."""
    assert CODE_TOOLS == (
        "mcp__code_intel__grep",
        "mcp__code_intel__read",
        "mcp__code_intel__batch_read",
        "mcp__code_intel__glob",
    )


@pytest.mark.asyncio
async def test_mcp_servers_and_code_tools_reach_the_provider_query() -> None:
    """CODE-LOOKUP AC2 — an Engine built with the grounding toolbelt threads BOTH
    the server config and the tool names onto the captured provider query: the
    turn that speaks is the turn that can actually read the clone (Law 1)."""
    sentinel = object()
    provider = FakeProvider()
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    engine = Engine(
        provider=provider,
        model=_MODEL,
        allowed_tools=CODE_TOOLS,
        speak=speak,
        disambiguate=_confirm_every_hit,
        map_text=_MAP,
        mcp_servers={"code_intel": sentinel},
    )

    engagement = await engine.feed_transcript(_line(_ASK))
    await engine.drain()

    assert engagement is not None
    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.mcp_servers == {"code_intel": sentinel}
    assert query.allowed_tools == CODE_TOOLS


@pytest.mark.asyncio
async def test_wake_turn_carries_a_multi_turn_budget() -> None:
    """The wake turn must NOT be capped at a single SDK turn: SPEC §4.3's "reason →
    plan → act, ONE PASS" is one WAKE, not one SDK step. A grounded turn needs several
    iterations (load a deferred tool → call it → observe → answer); max_turns=1 caps
    the agent right after the ack, before any tool result lands (the functional sim
    caught exactly this). Assert the ProviderQuery on the real wake path carries a
    multi-turn budget by default, and that a caller override threads through."""
    provider = FakeProvider()

    async def speak(_text: str) -> None:
        return None

    default_engine = Engine(
        provider=provider, model=_MODEL, allowed_tools=(), speak=speak,
        disambiguate=_confirm_every_hit, map_text=_MAP,
    )
    await default_engine.feed_transcript(_line(_ASK))
    await default_engine.drain()
    _, query = provider.calls[0]
    assert query.max_turns > 1  # never 1 — that caps the agent after the ack

    provider2 = FakeProvider()
    override_engine = Engine(
        provider=provider2, model=_MODEL, allowed_tools=(), speak=speak,
        disambiguate=_confirm_every_hit, map_text=_MAP, max_turns=25,
    )
    await override_engine.feed_transcript(_line(_ASK))
    await override_engine.drain()
    _, query2 = provider2.calls[0]
    assert query2.max_turns == 25


@pytest.mark.asyncio
async def test_engine_default_mcp_servers_is_none_backward_compat() -> None:
    """CODE-LOOKUP AC2 — an Engine built WITHOUT mcp_servers (the current callers)
    keeps the current behavior: the query carries no server config."""
    provider = FakeProvider()
    engine, _ = _engine(provider)

    await engine.feed_transcript(_line(_ASK))
    await engine.drain()

    assert len(provider.calls) == 1
    _, query = provider.calls[0]
    assert query.mcp_servers is None


# ── The pending-ask passthrough (mechanical, no NLP) ──────────────────────────


@pytest.mark.asyncio
async def test_arm_pending_ask_passthrough_wakes_on_the_reply() -> None:
    """``Engine.arm_pending_ask()`` is a pure passthrough to the trigger: after the
    caller arms it, the next un-prefixed human line wakes as the reply."""
    provider = FakeProvider()
    engine, _ = _engine(provider)

    engine.arm_pending_ask()
    engagement = await engine.feed_transcript(_line("Yes, the checkout retries.", "Priya", 31.0))
    await engine.drain()

    assert engagement is not None and engagement.source == "reply"
    assert len(provider.calls) == 1
    assert engine.last_turn is not None and engine.last_turn.source == "reply"
