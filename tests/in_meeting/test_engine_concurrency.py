"""Acceptance battery for ENGINE-CONCURRENCY — L7 per-ask isolation + W2 monitor-while-working.

SPEC §4/§9: "Monitoring while working is mandatory — the agent is never blocked,
never deaf." The Engine's wake turns now run as BACKGROUND TASKS: the feed path
(notes append + trigger consult) stays instant and never awaits a turn, and
concurrent asks each get their own isolated turn. ``Engine.drain()`` awaits all
in-flight turns; ``Engine.turns`` is every completed turn (completion order).

Deterministic and offline: the provider is a scripted fake whose stream BLOCKS
on an injected ``asyncio.Event`` before yielding — "a slow turn" with no clocks
and no wall-time flakiness; every in-flight assertion is structural.

Context-snapshot semantics (asserted honestly): ``build_turn_input`` runs inside
the wake task, so the turn's context is snapshotted when the task FIRST RUNS —
the first yield to the event loop after the feed call — not inside the feed call
itself. Once the turn is in flight (blocked at the provider), lines fed while it
works land in the notes immediately but NEVER enter that turn's context. The
snapshot is pinned from BOTH sides: lines fed after the first run are excluded
(AC1), and lines fed BEFORE the first run — both feeds in AC2 precede any yield
— DO appear in each turn's recent-notes block. The seven AC groups:

1. never deaf — lines fed while a turn is blocked in flight land in the notes
   instantly; the turn still completes and its context excludes those lines;
2. concurrent asks, per-ask isolation (S7) — two addressed lines back-to-back
   run two simultaneous turns; ``turns`` carries BOTH results, each with its
   OWN ask's answer (no cross-contamination); each turn's transcript block
   carries the OTHER ask's line (fed before either task first ran);
3. loop-not-blocked — the feed call returns while the turn is still in flight
   (structural: ``_inflight`` non-empty, release event not yet set);
4. drain with nothing in flight completes immediately; ``turns`` empty before
   any wake;
5. worker path concurrent — ``on_worker_done`` while a voice turn is in flight:
   both run simultaneously and both results land after drain;
6. one mouth — two concurrent multi-delta turns NEVER interleave their spoken
   deltas in the shared sink: the sink holds one whole turn's deltas
   contiguously, then the other's (speech serializes; Proxy has ONE voice);
7. pre-speech concurrency preserved — the speak lock serializes SPEECH only,
   never turn STARTS: a turn's tool-ish work before its first TEXT runs while
   another turn is already in flight (2 provider calls before anyone speaks).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from agentkit import ProviderQuery
from contracts import AgentChunk

from in_meeting.engine import Engine
from in_meeting.notes import TranscriptLine

_MODEL = "claude-opus-4-6"
_MAP = "# Map\n- auth in services/auth/login.py\n- retries in libs/http/client.py"

_ASK_A = "Proxy, where's the retry logic?"
_ASK_B = "Proxy, what handles auth?"
_ANSWER_A = "the retry logic is in client.py:42"
_ANSWER_B = "auth is handled in login.py:17"
_WORKER_ANSWER = "the background build came back green"
_DEFAULT_ANSWER = "on it"

_LATER_LINES: list[tuple[str, str, float]] = [
    ("Priya", "Meanwhile the checkout flake is back.", 21.0),
    ("Marcus", "It fails once then passes on retry.", 22.5),
    ("Devon", "Let's collect the failing run ids.", 24.0),
]


def _line(text: str, speaker: str = "Devon", timestamp: float = 20.0) -> TranscriptLine:
    return TranscriptLine(text=text, speaker=speaker, timestamp=timestamp, end_of_turn=True)


class SlowProvider:
    """A scripted ``agentkit.Provider`` whose stream BLOCKS on ``release`` before
    yielding — a deterministic "slow turn". Records every ``(prompt, query)``
    call at stream START (so in-flight turns are observable), and answers PER
    ASK: the reply is keyed on the ``You were addressed:`` section of the
    prompt, so each concurrent turn provably answers its own ask."""

    def __init__(
        self, release: asyncio.Event, answers: dict[str, str] | None = None
    ) -> None:
        self.calls: list[tuple[str, ProviderQuery]] = []
        self._release = release
        self._answers = answers or {}

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append((prompt, query))
        call_no = len(self.calls)
        await self._release.wait()
        ask = prompt.split("You were addressed:")[-1]
        answer = next(
            (text for key, text in self._answers.items() if key in ask), _DEFAULT_ANSWER
        )
        yield AgentChunk(type="TEXT", text=answer, metadata={"msg_id": f"m-{call_no}"})
        yield AgentChunk(type="RESULT", text=answer, metadata={})


def _engine(provider: SlowProvider) -> tuple[Engine, list[str]]:
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    engine = Engine(
        provider=provider,
        model=_MODEL,
        allowed_tools=(),
        speak=speak,
        disambiguate=lambda text: True,
        map_text=_MAP,
    )
    return engine, spoken


# ── AC1: never deaf — the notes keep growing while a turn is in flight ────────


@pytest.mark.asyncio
async def test_lines_land_in_the_notes_while_a_turn_is_in_flight() -> None:
    """AC1 — an addressed line starts a turn that blocks at the provider; three
    more lines fed WHILE it works land in the notes immediately, the feed calls
    return without waiting, and the turn still completes. The in-flight turn's
    context excludes the later lines (snapshotted at wake — see the module
    docstring for the honest timing)."""
    release = asyncio.Event()
    provider = SlowProvider(release, answers={_ASK_A: _ANSWER_A})
    engine, spoken = _engine(provider)

    engagement = await engine.feed_transcript(_line(_ASK_A))
    assert engagement is not None
    await asyncio.sleep(0)  # let the wake task start: snapshot context, block at provider
    assert len(provider.calls) == 1  # the turn IS in flight, blocked on the event

    before = len(engine.notes)
    for speaker, text, t in _LATER_LINES:
        assert await engine.feed_transcript(_line(text, speaker, t)) is None
    assert len(engine.notes) == before + 3  # every line landed WHILE the turn ran
    assert engine.turns == ()  # ...and the turn has not completed yet

    release.set()
    await engine.drain()

    assert len(engine.turns) == 1
    turn = engine.turns[0]
    assert turn.error is None
    assert turn.spoken == _ANSWER_A
    assert spoken == [_ANSWER_A]
    prompt, _ = provider.calls[0]
    assert _ASK_A in prompt
    for _, text, _t in _LATER_LINES:
        assert text not in prompt  # lines fed after the wake never entered this turn


# ── AC2: concurrent asks — per-ask isolation (the S7 scenario) ────────────────


@pytest.mark.asyncio
async def test_two_concurrent_asks_each_get_their_own_isolated_turn() -> None:
    """AC2 — two addressed lines back-to-back with a slow provider: BOTH turns
    run simultaneously; after drain ``turns`` carries both results, each with
    its OWN ask's answer — no result lost, no cross-contamination."""
    release = asyncio.Event()
    provider = SlowProvider(release, answers={_ASK_A: _ANSWER_A, _ASK_B: _ANSWER_B})
    engine, spoken = _engine(provider)

    assert await engine.feed_transcript(_line(_ASK_A, "Devon", 20.0)) is not None
    assert await engine.feed_transcript(_line(_ASK_B, "Priya", 21.0)) is not None
    await asyncio.sleep(0)  # let both wake tasks start
    assert len(provider.calls) == 2  # two turns in flight AT THE SAME TIME
    assert engine.turns == ()

    release.set()
    await engine.drain()

    assert len(engine.turns) == 2
    assert {t.spoken for t in engine.turns} == {_ANSWER_A, _ANSWER_B}
    assert all(t.error is None and t.source == "voice" for t in engine.turns)
    assert sorted(spoken) == sorted([_ANSWER_A, _ANSWER_B])
    # Each turn's ASK is its own — the volatile ask section never bleeds across.
    asks = [prompt.split("You were addressed:")[-1] for prompt, _ in provider.calls]
    assert any(_ASK_A in a and _ASK_B not in a for a in asks)
    assert any(_ASK_B in a and _ASK_A not in a for a in asks)
    # Positive snapshot pin: BOTH feeds preceded any yield to the event loop, so
    # each line was in the notes when either turn's task FIRST RAN — ask B's line
    # IS in turn A's recent-notes transcript block (and vice versa). This pins
    # the documented snapshot timing from the inclusion side; AC1 pins exclusion.
    prompt_a = next(p for p, _ in provider.calls if _ASK_A in p.split("You were addressed:")[-1])
    prompt_b = next(p for p, _ in provider.calls if _ASK_B in p.split("You were addressed:")[-1])
    assert _ASK_B in prompt_a.split("You were addressed:")[0]
    assert _ASK_A in prompt_b.split("You were addressed:")[0]
    # last_turn is the most recently COMPLETED turn — one of the two, never lost.
    assert engine.last_turn is not None and engine.last_turn in engine.turns


# ── AC3: the feed path never blocks on a running turn ─────────────────────────


@pytest.mark.asyncio
async def test_feed_returns_while_the_turn_is_still_in_flight() -> None:
    """AC3 — structural not-blocked proof: ``feed_transcript`` has RETURNED while
    the turn's task is still in ``_inflight`` and the provider's release event
    has never been set (no wall-clock, no flakiness)."""
    release = asyncio.Event()
    provider = SlowProvider(release, answers={_ASK_A: _ANSWER_A})
    engine, _ = _engine(provider)

    engagement = await engine.feed_transcript(_line(_ASK_A))

    # The feed call returned; the turn is provably still in flight.
    assert engagement is not None
    assert len(engine._inflight) == 1  # noqa: SLF001 — the structural in-flight probe
    assert not release.is_set()
    assert engine.turns == ()

    release.set()
    await engine.drain()
    assert len(engine._inflight) == 0  # noqa: SLF001
    assert len(engine.turns) == 1


# ── AC4: drain is safe with nothing in flight ─────────────────────────────────


@pytest.mark.asyncio
async def test_drain_with_nothing_in_flight_completes_and_turns_is_empty() -> None:
    """AC4 — ``drain()`` before any wake completes immediately (nothing to
    await); ``turns`` is empty and no provider work happened."""
    release = asyncio.Event()
    provider = SlowProvider(release)
    engine, spoken = _engine(provider)

    assert engine.turns == ()
    await engine.drain()  # must complete with nothing in flight

    assert engine.turns == ()
    assert engine.last_turn is None
    assert provider.calls == []
    assert spoken == []


# ── AC5: the worker path is concurrent too ────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_delivery_runs_concurrently_with_an_in_flight_turn() -> None:
    """AC5 — ``on_worker_done`` while a voice turn is blocked in flight: both
    turns run simultaneously and BOTH results land after drain, each carrying
    its own payload's answer."""
    release = asyncio.Event()
    provider = SlowProvider(release, answers={_ASK_A: _ANSWER_A, "w-7": _WORKER_ANSWER})
    engine, _ = _engine(provider)

    assert await engine.feed_transcript(_line(_ASK_A)) is not None
    await asyncio.sleep(0)  # the voice turn is in flight, blocked at the provider
    assert len(provider.calls) == 1

    engagement = await engine.on_worker_done("w-7", "build green: 128 tests passed")
    assert engagement.source == "worker"
    await asyncio.sleep(0)  # let the worker wake task start too
    assert len(provider.calls) == 2  # both turns in flight AT THE SAME TIME
    assert engine.turns == ()

    release.set()
    await engine.drain()

    assert len(engine.turns) == 2
    by_source = {t.source: t for t in engine.turns}
    assert by_source["voice"].spoken == _ANSWER_A
    assert by_source["worker"].spoken == _WORKER_ANSWER
    assert all(t.error is None for t in engine.turns)


# ── AC6 + AC7: one mouth — speech serializes, pre-speech work does not ────────

_DELTAS_A = ["A1 retry ", "A2 logic ", "A3 client.py:42"]
_DELTAS_B = ["B1 auth ", "B2 lives ", "B3 login.py:17"]


class MultiDeltaProvider:
    """A scripted provider that streams SEVERAL accumulated TEXT chunks per turn
    with an explicit event-loop yield (``asyncio.sleep(0)``) between each — so
    two concurrent turns WOULD provably interleave their spoken deltas in the
    shared sink if speech were not serialized. The reply script is keyed on the
    ``You were addressed:`` ask, exactly like :class:`SlowProvider`. Turns with
    ``pre_speech`` scripts first emit TOOL_USE/TOOL_RESULT chunks (recorded in
    ``tool_steps``) and then block on ``gate`` BEFORE their first TEXT — the
    fake tool-ish progression AC7 uses to prove pre-speech work never waits on
    another turn's speech."""

    def __init__(
        self,
        scripts: dict[str, list[str]],
        *,
        pre_speech: dict[str, int] | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.tool_steps: list[str] = []
        self._scripts = scripts
        self._pre_speech = pre_speech or {}
        self._gate = gate

    async def stream(self, prompt: str, query: ProviderQuery) -> AsyncIterator[AgentChunk]:
        self.calls.append(prompt)
        msg_id = f"m-{len(self.calls)}"
        ask = prompt.split("You were addressed:")[-1]
        deltas = next(
            (script for key, script in self._scripts.items() if key in ask), []
        )
        tool_steps = next(
            (n for key, n in self._pre_speech.items() if key in ask), 0
        )
        for step in range(tool_steps):
            await asyncio.sleep(0)  # tool-ish pre-speech work yields to the loop
            self.tool_steps.append(f"{msg_id}-tool-{step}")
            yield AgentChunk(
                type="TOOL_USE", text=None,
                metadata={"id": f"t-{step}", "name": "mcp__code_intel__grep", "input": {}},
            )
            yield AgentChunk(
                type="TOOL_RESULT", text=None,
                metadata={"tool_use_id": f"t-{step}", "is_error": False, "structured": None},
            )
        if self._gate is not None:
            await self._gate.wait()
        accumulated = ""
        for delta in deltas:
            await asyncio.sleep(0)  # the interleave point: yield BETWEEN spoken deltas
            accumulated += delta
            yield AgentChunk(type="TEXT", text=accumulated, metadata={"msg_id": msg_id})
        yield AgentChunk(type="RESULT", text=accumulated, metadata={})


def _multi_engine(provider: MultiDeltaProvider) -> tuple[Engine, list[str]]:
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    engine = Engine(
        provider=provider,
        model=_MODEL,
        allowed_tools=(),
        speak=speak,
        disambiguate=lambda text: True,
        map_text=_MAP,
    )
    return engine, spoken


@pytest.mark.asyncio
async def test_concurrent_turns_never_interleave_spoken_deltas() -> None:
    """AC6 — one mouth: two simultaneous multi-delta turns, each yielding to the
    event loop between deltas (so an unserialized engine provably interleaves
    them). The shared sink must hold one turn's deltas CONTIGUOUSLY, then the
    other's — whichever turn spoke first, never a mix. ``TurnResult.spoken``
    stays each turn's own full text, unchanged."""
    provider = MultiDeltaProvider({_ASK_A: _DELTAS_A, _ASK_B: _DELTAS_B})
    engine, spoken = _multi_engine(provider)

    assert await engine.feed_transcript(_line(_ASK_A, "Devon", 20.0)) is not None
    assert await engine.feed_transcript(_line(_ASK_B, "Priya", 21.0)) is not None
    await asyncio.sleep(0)
    assert len(provider.calls) == 2  # both turns in flight at the same time

    await engine.drain()

    # The whole-sink sequence is one turn then the other — NO interleave.
    assert spoken in (_DELTAS_A + _DELTAS_B, _DELTAS_B + _DELTAS_A), (
        f"spoken deltas interleaved across turns: {spoken!r}"
    )
    # Per-turn results are intact and their own (semantics unchanged).
    assert {t.spoken for t in engine.turns} == {"".join(_DELTAS_A), "".join(_DELTAS_B)}
    assert all(t.error is None for t in engine.turns)


@pytest.mark.asyncio
async def test_pre_speech_work_runs_concurrently_speech_lock_never_serializes_starts() -> None:
    """AC7 — the speak lock serializes SPEECH, never turn STARTS or pre-speech
    work: with both turns gated before their first TEXT, BOTH providers are
    called and turn B's tool-ish progression completes while NOTHING has been
    spoken yet — 2 in flight before either speaks. After the gate opens, speech
    still lands contiguously per turn."""
    gate = asyncio.Event()
    provider = MultiDeltaProvider(
        {_ASK_A: _DELTAS_A, _ASK_B: _DELTAS_B},
        pre_speech={_ASK_B: 2},
        gate=gate,
    )
    engine, spoken = _multi_engine(provider)

    assert await engine.feed_transcript(_line(_ASK_A, "Devon", 20.0)) is not None
    assert await engine.feed_transcript(_line(_ASK_B, "Priya", 21.0)) is not None
    for _ in range(6):  # let both streams start and B's tool progression run
        await asyncio.sleep(0)

    # Both providers CALLED concurrently, B's pre-speech tool work done — and
    # not one delta spoken: the lock gated nothing before the first TEXT.
    assert len(provider.calls) == 2
    assert len(provider.tool_steps) == 2
    assert spoken == []
    assert engine.turns == ()

    gate.set()
    await engine.drain()

    assert spoken in (_DELTAS_A + _DELTAS_B, _DELTAS_B + _DELTAS_A)
    assert {t.spoken for t in engine.turns} == {"".join(_DELTAS_A), "".join(_DELTAS_B)}
    assert all(t.error is None for t in engine.turns)
