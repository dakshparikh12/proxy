"""The engine loop / wake handoff — the integration spine (Task L8, SPEC §3/§4/§9).

Proxy is ONE agent session living in a meeting, and this module is the always-on
loop that ties the merged pieces together: every transcript line accumulates as
notes (M1) and feeds the engagement trigger (M2); when the trigger fires, Proxy
wakes with EVERYTHING — the prime (L5) + the ``index.md`` map riding the stable
cached prefix and the recent notes + the ask riding the volatile prompt (L1) —
runs ONE streamed provider turn (L2), routes the spoken TEXT to the injected
``speak`` sink, and returns to listening. "Claude Code, pointed at a meeting."

The Engine routes PHYSICS only — append, consult, stream, emit. It makes no
situation→action decision of its own: the trigger says WHEN Proxy wakes, and the
agent + prompt decide WHAT to do. The ``source`` on an :class:`Engagement` is
provenance, never a behavior branch — the wake path is one code path for voice,
chat, reply, and worker alike; only the *payload fields the engagement carries*
are rendered forward verbatim into the ask.

Failure is graceful by construction (SPEC §9): an ``ERROR`` chunk or a provider
exception is surfaced honestly on the :class:`TurnResult` and the loop CONTINUES
— a failed turn never crashes the Engine, and the next line still wakes it.
Idle is free: an input the trigger declines does ZERO provider work. Detecting
whether Proxy's turn asked a clarifying question is the AGENT's judgment, not
string physics — the Engine only exposes :meth:`Engine.arm_pending_ask` as a
mechanical passthrough for the caller to arm the follow-up window (no "?"
parsing, no NLP, lives here).

Never blocked, never deaf (L7/W2, SPEC §4/§9): wake turns run as BACKGROUND
TASKS. The feed path — notes append + trigger consult — is instant and never
awaits a turn, so the loop keeps ingesting while Proxy works, and two asks a
second apart run as two simultaneous turns, each isolated per ask (every turn
carries its own local context/stream state; nothing of one leaks into another).
Every completed turn lands in :attr:`Engine.turns` (completion order — which
may differ from ask order); :attr:`Engine.last_turn` is the most recently
COMPLETED turn; :meth:`Engine.drain` awaits everything in flight (tests +
orderly meeting end). A turn's context is snapshotted when its task first runs
— the first yield to the event loop after the feed call — so lines that arrive
while a turn is already working never mutate that turn's ask.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agentkit import Provider

from in_meeting.context import build_turn_input
from in_meeting.notes import NotesStore, TranscriptLine
from in_meeting.prompt import PROXY_SYSTEM_PROMPT
from in_meeting.provider import EngineProvider
from in_meeting.trigger import (
    ChatLine,
    Disambiguate,
    Engagement,
    EngagementTrigger,
    Source,
)

#: The grounded code-lookup toolbelt — the four canonical ``mcp__code_intel__*``
#: names premeeting's ``RepoContext.build_server()`` exposes over the clone
#: (``SERVER_NAME="code_intel"``, ``TOOL_BASENAMES=("grep","read","batch_read",
#: "glob")``). Callers pass this as ``allowed_tools`` alongside
#: ``mcp_servers={"code_intel": <the built server>}`` — the Engine itself stays
#: decoupled from premeeting and just threads what it's given.
CODE_TOOLS: tuple[str, ...] = (
    "mcp__code_intel__grep",
    "mcp__code_intel__read",
    "mcp__code_intel__batch_read",
    "mcp__code_intel__glob",
)

#: The speak sink as a plain async callable — receives each spoken text delta.
SpeakFn = Callable[[str], Awaitable[None]]


@runtime_checkable
class SpeakSink(Protocol):
    """The alternative speak shape: an object carrying an async ``say``."""

    def say(self, text: str) -> Awaitable[None]: ...


def _as_speak_fn(speak: SpeakFn | SpeakSink) -> SpeakFn:
    """Normalize either speak shape to the one async callable the loop drives."""
    if isinstance(speak, SpeakSink):
        return speak.say
    return speak


@dataclass(frozen=True, slots=True)
class TurnResult:
    """One wake turn's outcome, stated honestly.

    ``spoken`` is EXACTLY the text routed to the speak sink (the concatenation
    of the deltas, in order); ``result_text`` is the terminal ``RESULT`` chunk's
    text when one arrived; ``error`` is the honest fault (an ``ERROR`` chunk's
    message or a raised exception, never re-thrown) — ``None`` on success.
    ``source`` is the provenance of the engagement that woke the turn.
    """

    source: Source
    spoken: str
    result_text: str = ""
    error: str | None = None


def _render_ask(engagement: Engagement) -> str:
    """Carry the engagement's payload forward VERBATIM as the turn's ask.

    Mechanical rendering on payload PRESENCE, never a branch on ``source``:
    whatever fields the trigger populated (``text`` for a spoken/chat/reply ask,
    ``worker_id``/``result`` for a finished worker's delivery) are handed to the
    agent unmodified — the Engine never re-parses or interprets them.
    """
    parts: list[str] = []
    if engagement.text:
        parts.append(engagement.text)
    if engagement.worker_id or engagement.result:
        parts.append(
            f"Background worker {engagement.worker_id!r} finished. Its result:\n"
            f"{engagement.result}"
        )
    return "\n\n".join(parts)


class Engine:
    """The always-on loop of ONE meeting: accumulate → watch → wake → speak → resume.

    A thin coordinator over the merged seams, with everything that judges or
    talks to the world INJECTED: ``provider`` is any ``agentkit.Provider``
    (default: the engine's real :class:`~in_meeting.provider.EngineProvider`;
    tests pass a scripted fake); ``speak`` is the async sink Proxy's spoken text
    streams to (production wires the TTS→meeting channel, C9); ``disambiguate``
    is the trigger's one judgment hook ("addressed to me, or 'proxy server'?").
    ``map_text=None`` (unindexed repo) degrades to a prime-only prefix and must
    still run — nothing here requires a map. ``mcp_servers`` is the injected
    server-name → SDK MCP server config mapping the caller mounts for the turn
    (e.g. ``{"code_intel": ...}`` with ``allowed_tools=CODE_TOOLS``) — pure
    threading, no capability decision here; ``None`` mounts nothing.
    """

    def __init__(
        self,
        *,
        model: str,
        allowed_tools: tuple[str, ...],
        speak: SpeakFn | SpeakSink,
        disambiguate: Disambiguate,
        provider: Provider | None = None,
        map_text: str | None = None,
        prime: str = PROXY_SYSTEM_PROMPT,
        recent_lines: int = 40,
        mcp_servers: dict[str, Any] | None = None,
        max_turns: int = 16,
    ) -> None:
        self._model = model
        self._allowed_tools = allowed_tools
        self._mcp_servers = mcp_servers
        self._max_turns = max_turns
        self._speak: SpeakFn = _as_speak_fn(speak)
        self._provider: Provider = provider if provider is not None else EngineProvider()
        self._map_text = map_text
        self._prime = prime
        self._recent_lines = recent_lines
        self._notes = NotesStore()
        self._trigger = EngagementTrigger(disambiguate=disambiguate)
        self._last_turn: TurnResult | None = None
        self._turns: list[TurnResult] = []
        self._inflight: set[asyncio.Task[TurnResult]] = set()

    @property
    def notes(self) -> NotesStore:
        """The meeting's running notes (the raw transcript) — the M1 store."""
        return self._notes

    @property
    def last_turn(self) -> TurnResult | None:
        """The most recently COMPLETED wake turn's outcome; ``None`` before any
        completes. With concurrent turns, completion order may differ from ask
        order — :attr:`turns` keeps every result."""
        return self._last_turn

    @property
    def turns(self) -> tuple[TurnResult, ...]:
        """Every completed wake turn, in COMPLETION order (a snapshot).

        With overlapping turns no result is ever lost to the ``last_turn``
        overwrite — each turn appends here the moment it finishes.
        """
        return tuple(self._turns)

    def arm_pending_ask(self) -> None:
        """Passthrough to the trigger's follow-up window (M2).

        The CALLER arms this after Proxy asks a clarifying question — deciding
        that a question was asked is the agent's judgment, never the Engine's
        string physics.
        """
        self._trigger.arm_pending_ask()

    async def feed_transcript(self, line: TranscriptLine) -> Engagement | None:
        """One spoken line: append to the notes, consult the trigger, wake if engaged.

        Returns the :class:`Engagement` when Proxy woke — the turn runs as a
        BACKGROUND task (never awaited here; the loop stays listening, L7/W2);
        ``await drain()`` then read :attr:`turns` / :attr:`last_turn` for its
        outcome. ``None`` when idle: free, zero provider work.
        """
        self._notes.append(line)
        engagement = self._trigger.on_transcript(line)
        if engagement is None:
            return None
        self._spawn_turn(engagement)
        return engagement

    async def feed_chat(self, msg: ChatLine) -> Engagement | None:
        """One chat message: consult the trigger, wake on the ``@proxy`` token.

        The wake turn runs as a background task, exactly like the voice path.
        """
        engagement = self._trigger.on_chat(msg)
        if engagement is None:
            return None
        self._spawn_turn(engagement)
        return engagement

    async def on_worker_done(self, worker_id: str, result: str) -> Engagement:
        """A finished background worker: always a wake carrying its result.

        The delivery turn runs as a background task too — a worker landing
        while another turn is mid-flight never waits and never blocks the loop.
        """
        engagement = self._trigger.on_worker_done(worker_id, result)
        self._spawn_turn(engagement)
        return engagement

    def _spawn_turn(self, engagement: Engagement) -> None:
        """Start one wake turn as a tracked background task (the L7/W2 seam).

        The task is held in ``_inflight`` until it finishes (the done-callback
        discards it and retrieves the outcome — ``_wake_and_run`` never raises
        (§9), so no exception can leak or go unretrieved). The feed path returns
        the instant the task is created.
        """
        task = asyncio.create_task(self._wake_and_run(engagement))
        self._inflight.add(task)
        task.add_done_callback(self._on_turn_task_done)

    def _on_turn_task_done(self, task: asyncio.Task[TurnResult]) -> None:
        """Untrack a finished turn task and retrieve its outcome.

        ``_wake_and_run`` never raises, so ``exception()`` is ``None`` by
        construction — retrieving it keeps that guarantee observable (no
        "exception was never retrieved" warning could ever fire).
        """
        self._inflight.discard(task)
        if not task.cancelled():
            task.exception()

    async def drain(self) -> None:
        """Await every in-flight wake turn — tests + orderly meeting end.

        Loops until ``_inflight`` is empty so turns spawned while draining
        (e.g. a worker finishing mid-drain) are awaited too. Never raises:
        ``_wake_and_run`` absorbs all turn faults into honest results (§9).
        """
        while self._inflight:
            await asyncio.gather(*tuple(self._inflight))

    async def _wake_and_run(self, engagement: Engagement) -> TurnResult:
        """ONE fully-contexted streamed turn; never raises — the loop must survive.

        Physics only: assemble the turn input (L1), stream the provider (L2),
        route each TEXT chunk's NEW SUFFIX to ``speak`` (the provider's TEXT
        carries ACCUMULATED text per ``msg_id`` — provider.py §1.1 — so the
        Engine speaks each fragment exactly once, never twice), keep the
        terminal RESULT text, and turn any ERROR chunk or raised exception into
        an honest :class:`TurnResult` error.

        Per-ask isolated (L7): everything a turn accumulates — ``turn_input``,
        the ``seen`` per-msg text, ``spoken_parts``, ``result_text``, ``error``
        — is LOCAL to this call, so overlapping turns never share stream state.
        The turn's context is snapshotted HERE (the ``build_turn_input`` call
        when the task first runs); lines landing in the notes after that never
        enter this turn. On completion the result appends to ``_turns`` and
        becomes ``_last_turn`` — the only shared writes, both at the end.
        """
        spoken_parts: list[str] = []
        result_text = ""
        error: str | None = None
        try:
            turn_input = build_turn_input(
                prime=self._prime,
                map_text=self._map_text,
                notes=self._notes,
                ask=_render_ask(engagement),
                model=self._model,
                allowed_tools=self._allowed_tools,
                recent_lines=self._recent_lines,
                mcp_servers=self._mcp_servers,
                max_turns=self._max_turns,
            )
            # Accumulated-text-so-far per msg_id — spoken deltas are the new suffix.
            seen: dict[str, str] = {}
            async for chunk in self._provider.stream(turn_input.prompt, turn_input.query):
                if chunk.type == "TEXT":
                    msg_id = str(chunk.metadata.get("msg_id", ""))
                    accumulated = chunk.text or ""
                    delta = accumulated[len(seen.get(msg_id, "")):]
                    seen[msg_id] = accumulated
                    if delta:
                        spoken_parts.append(delta)
                        await self._speak(delta)
                elif chunk.type == "RESULT":
                    result_text = chunk.text or ""
                elif chunk.type == "ERROR" and error is None:
                    # The FIRST error is the root cause; keep draining so a
                    # trailing RESULT (if any) still lands.
                    error = str(chunk.metadata.get("message", "")) or "provider error"
        except Exception as exc:  # noqa: BLE001 — a failed turn NEVER crashes the loop (§9)
            error = str(exc) or exc.__class__.__name__
        turn = TurnResult(
            source=engagement.source,
            spoken="".join(spoken_parts),
            result_text=result_text,
            error=error,
        )
        self._turns.append(turn)
        self._last_turn = turn
        return turn
