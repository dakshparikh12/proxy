"""The wake turn — the whole agent, precisely (node ``orchestrator.wake-turn``, §3.2).

ONE persistent Claude SDK session per meeting. A **wake** is ``(event + a compacted
state-digest)`` in → **tool calls** out, streaming through the provider seam via
:class:`~agentkit.BehaviorRunner` with a mounted behavior. This module is the thin
assembly that owns the meeting-scoped session lifecycle *above* the seam — it holds
no vendor client and makes no SDK call itself; the injected provider (the seam) is
the only thing that talks to a model.

What this node owns, and nothing more (§3.2):

* **One persistent session, resumed each wake (§3.5 Tier-1).** The first wake
  carries no resume; its ``INIT`` chunk exposes the SDK ``session_id``, which is
  captured (:attr:`WakeTurn.session_id`) and handed back as ``resume`` on every
  later wake. A gone session after a recycle is recovered by the inherited §3.5
  :func:`~agentkit.resume_with_fallback` (rebuild from the transcript-plane
  ``history_fn``, emit the restored notice, retry WITHOUT resume) — this module
  passes that fallback its ``history_fn`` and re-points :attr:`session_id` at the
  new session the retry establishes.

* **Event + a COMPACT state-digest in (§3.2 / CANONICAL §10.2).** The turn is
  primed by the compact :class:`StateDigest` (tasks in flight, mouth free/busy,
  component health) — never raw session history. The digest is **regenerated every
  ``~15`` wakes OR on a material-state change** and reused in between, so a
  sporadic wake pays only the event, not a fresh digest.

* **Notes are the durable memory read via ``GET /internal/notes`` (CANONICAL
  §11.4).** The turn carries ``notes_ref = meeting_id`` — a HANDLE, never the notes
  object — and reads the live notes on demand through the injected internal reader.
  The notes object is NEVER embedded in the session history, so a compaction can
  never drop meeting state.

* **Tool calls out through the seam (§3.4).** The model's reply is the seam's typed
  ``AgentChunk`` stream; the wake yields it straight through (barge-in/TTS read the
  ``TEXT`` deltas, the harness routes the ``TOOL_USE`` chunks to its registered tool
  functions). There is NO ``if event → action`` branch here: the situation→action
  mapping lives entirely in the model's turn (Law 4).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentkit import BehaviorRunner, resume_with_fallback
from libs.llm.prompts import fence_untrusted

from libs.contracts import AgentChunk

# The digest is regenerated every ~15 wakes (CANONICAL §10.2 — "regenerated every
# [~15] wakes or on a material-state change"), the same rolling-summary cadence
# Doc 03 uses for the Scribe's Segment B. Tunable, pinned here as the one default.
DEFAULT_COMPACTION_EVERY = 15

# The wake-behavior this node mounts by default. It is the normative direct-answer
# + dispatch envelope (§3.4); a real harness may select a different registered
# behavior per wake — selecting a behavior by name IS the branch (D-023).
DEFAULT_BEHAVIOR = "answer-question"


@dataclass(frozen=True)
class WakeEvent:
    """One wake payload: the ask verbatim + who said it + when (§3.2).

    The event is DATA fed to the model, never a control channel — an injected
    'ignore your rules' in ``text`` reaches no outward side-effect (the transcript
    is untrusted; every world-touching act is a staged draft behind a human click).
    """

    text: str
    speaker: str = ""
    ts: str = ""

    def render(self) -> str:
        """Render the event as a compact prompt line (the volatile tail, §3.2)."""
        who = f" ({self.speaker})" if self.speaker else ""
        when = f" @{self.ts}" if self.ts else ""
        return f"event{who}{when}: {self.text}"


@dataclass(frozen=True)
class StateDigest:
    """The compact state digest that primes a wake (§3.2) — NOT raw history.

    A small bounded summary: tasks in flight, mouth free/busy, component health.
    It is the cached stable prefix (§3.2 / CANONICAL §10.1) — regenerated on the
    compaction cadence, reused in between — so a sporadic wake pays only the event.
    """

    tasks_in_flight: tuple[str, ...] = ()
    mouth_busy: bool = False
    component_health: str = "all green"

    def render(self) -> str:
        """Render the digest as a compact, human-readable block (bounded, no raw
        transcript). This is the whole 'state' the model is primed with — the notes
        object is NOT here (it is read on demand via the internal reader)."""
        tasks = ", ".join(self.tasks_in_flight) if self.tasks_in_flight else "none"
        mouth = "busy" if self.mouth_busy else "free"
        return (
            "state digest:\n"
            f"  - tasks in flight: {tasks}\n"
            f"  - mouth: {mouth}\n"
            f"  - component health: {self.component_health}"
        )


@dataclass(frozen=True)
class ToolCall:
    """A tool call the model produced this turn (surfaced for convenience)."""

    name: str
    input: dict[str, Any] = field(default_factory=dict)
    id: str = ""


def _default_regenerate() -> StateDigest:
    """The no-op digest source used when no regenerator is injected: an empty
    default digest. A real harness injects a regenerator that reads the live task
    table / mouth state / component health."""
    return StateDigest()


async def _empty_history() -> str:
    """The default transcript-plane reader when none is injected: no prior meeting.

    A real harness passes Doc 03's transcript-plane reader (§3.5); with none, a
    stale-session replay rebuilds from an empty preamble (still emits the notice)."""
    return ""


class _Abort:
    """A minimal abort handle for a wake (``.aborted`` is what §3.5 reads)."""

    __slots__ = ("aborted",)

    def __init__(self, aborted: bool = False) -> None:
        self.aborted = aborted


class WakeTurn:
    """One meeting's persistent wake-turn session (§3.2).

    Holds the meeting-scoped session pointer, the compacted state digest and its
    compaction cadence, and the injected seams (provider, notes reader, history
    reader, digest regenerator). :meth:`wake` runs ONE wake — ``(event + digest)``
    in → tool-call ``AgentChunk`` stream out — resuming the persisted session and
    recovering a recycle via the inherited §3.5 fallback.

    Everything above the provider seam is real; only the ``provider`` is injected
    (a fake in tests, the real Claude-SDK provider in production) — no SDK call is
    made by this module.
    """

    def __init__(
        self,
        *,
        meeting_id: str,
        provider: Any,
        behavior: str = DEFAULT_BEHAVIOR,
        registry: Mapping[str, Any] | None = None,
        digest: StateDigest | None = None,
        regenerate_digest: Callable[[], StateDigest] | None = None,
        notes_reader: Callable[[str], Awaitable[str]] | None = None,
        history_fn: Callable[[], Awaitable[Any]] | None = None,
        compaction_every: int = DEFAULT_COMPACTION_EVERY,
        cost_meter: Any | None = None,
        mcp_servers: Mapping[str, Any] | None = None,
    ) -> None:
        self.meeting_id = meeting_id
        self._behavior = behavior
        self._registry = self._resolve_registry(registry)
        # The curated MCP servers whose tools the mounted behaviors' ``allowed_tools`` name —
        # e.g. this meeting's ``code_intel`` server, so ``mcp__code_intel__*`` is actually
        # MOUNTED and the wake turn can answer a grounded codebase question (the seam gap this
        # closes). Threaded straight to the runner (which threads it to every ProviderQuery).
        # ``None`` = no servers (a behavior with only delivery verbs needs none) — the runner
        # then builds queries with no mcp_servers, exactly as before.
        self._mcp_servers = dict(mcp_servers) if mcp_servers else None
        self._runner = BehaviorRunner(
            registry=self._registry,
            provider=provider,
            cost_meter=cost_meter,
            mcp_servers=self._mcp_servers,
        )
        self._regenerate = regenerate_digest or _default_regenerate
        # The initial digest: an explicit one, else built from the regenerator.
        self._digest = digest if digest is not None else self._regenerate()
        self._notes_reader = notes_reader
        self._history_fn = history_fn or _empty_history
        self._compaction_every = max(1, compaction_every)

        # Meeting-scoped session pointer (§3.5 Tier-1). None until the first INIT.
        self.session_id: str | None = None
        # Wake bookkeeping for the compaction cadence.
        self.wake_count = 0
        self._wakes_since_compaction = 0
        self._material_change = False
        # The tool calls the last wake produced (surfaced for the harness).
        self.last_tool_calls: list[ToolCall] = []

    # -- construction helpers --------------------------------------------------

    @staticmethod
    def _resolve_registry(registry: Mapping[str, Any] | None) -> dict[str, Any]:
        """Use the injected registry, else the harness behavior registry (§3.4)."""
        if registry is not None:
            return dict(registry)
        from .behaviors import REGISTRY

        return dict(REGISTRY)

    # -- compaction ------------------------------------------------------------

    def mark_material_change(self) -> None:
        """Flag a material-state change (a task completed, component health flipped,
        a preference landed) — the NEXT wake regenerates the digest immediately
        rather than waiting for the ~15-wake cadence (CANONICAL §10.2)."""
        self._material_change = True

    def _digest_for_this_wake(self) -> StateDigest:
        """Return the digest priming this wake, regenerating it if the cadence is due
        or a material change is pending; otherwise reuse the cached digest (§3.2)."""
        due = self._wakes_since_compaction >= self._compaction_every
        if self._material_change or due:
            self._digest = self._regenerate()
            self._wakes_since_compaction = 0
            self._material_change = False
        return self._digest

    # -- notes (durable memory, read on demand via GET /internal/notes) --------

    async def _read_notes(self) -> str:
        """Read the live notes via the internal reader (``notes_ref = meeting_id``).

        CANONICAL §11.4: the notes object is folded from Postgres and fetched fresh
        THROUGH the reader — never carried inline in the session history. With no
        reader injected this returns ``""`` (the notes ride only as the ``notes_ref``
        handle on the prompt)."""
        if self._notes_reader is None:
            return ""
        return await self._notes_reader(self.meeting_id)

    # -- the wake --------------------------------------------------------------

    async def wake(
        self,
        event: WakeEvent,
        *,
        read_notes: bool = False,
        abort: Any = None,
        behavior: str | None = None,
    ) -> AsyncIterator[AgentChunk]:
        """Run ONE wake: ``(event + compacted digest)`` in → tool-call stream out.

        Resolves the digest for this wake (regenerating on cadence / material
        change), reads live notes on demand (durable memory, never inline), mounts
        the wake-behavior, resumes the persistent session, and streams the seam's
        typed ``AgentChunk`` output straight through. The ``INIT`` chunk's
        ``session_id`` is captured to :attr:`session_id`; a recycle is recovered by
        the inherited §3.5 fallback, which re-points :attr:`session_id` at the new
        session the retry establishes.

        ``behavior`` selects the registered wake-behavior for THIS wake (§3.4 — "a real
        harness may select a different registered behavior per wake"; selecting a behavior
        by name IS the branch, D-023). Defaults to the construction-time
        :attr:`_behavior`, so a caller that mounts one behavior for the whole session (the
        existing callers) is unchanged; the live-brain adapter passes the per-ask
        selection. The persistent session pointer is shared across behaviors (ONE session
        per meeting, §3.5) — only the mounted role/tool-subset changes per wake.
        """
        self.wake_count += 1
        self._wakes_since_compaction += 1
        self.last_tool_calls = []

        digest = self._digest_for_this_wake()
        notes = await self._read_notes() if read_notes else ""

        # ``notes_ref`` is the meeting_id HANDLE (CANONICAL §1.3 / §11.4), never the
        # object. When the notes are read on demand for this turn, the freshly folded
        # text rides ALONGSIDE the handle on the same declared ``notes_ref`` input —
        # so the durable memory reaches the model without ever entering the session
        # history the compaction summarizes (it is re-read fresh each turn, never
        # persisted into the running session). The handle itself is always present.
        # Both the wake event and the folded notes are UNTRUSTED meeting-derived data
        # (a participant may plant 'ignore your rules' / 'open a PR' in either). Fence
        # each in the shared ``<untrusted-transcript>`` spotlight delimiters — the same
        # idiom the Scribe/Workroom use (§10.3 / 04 §3.4) — so the model sees a hard
        # data/instruction boundary; the injection guardrail on the system prompt
        # (build_query) names these delimiters as untrusted data whose embedded
        # instructions are never followed. The meeting_id HANDLE stays outside the fence.
        notes_ref: str = self.meeting_id
        if notes:
            notes_ref = (
                f"{self.meeting_id}\nlive notes (read via GET /internal/notes):\n"
                f"{fence_untrusted(notes)}"
            )
        inputs: dict[str, Any] = {
            "event": fence_untrusted(event.render()),
            "state_digest": digest.render(),
            "notes_ref": notes_ref,
        }

        abort_handle = abort if abort is not None else _Abort()

        # The behavior mounted for THIS wake — the per-ask selection (§3.4 / D-023) when the
        # live-brain adapter passes one, else the session default. An unknown name falls
        # back to the default so a selection bug can never mount a missing behavior.
        mounted = behavior if (behavior and behavior in self._registry) else self._behavior

        # One persistent session: resume the captured id (None on the first wake),
        # recovering a recycle via the inherited §3.5 transcript-plane replay.
        async for chunk in resume_with_fallback(
            self._runner,
            mounted,
            inputs,
            self.session_id,
            abort_handle,
            self._history_fn,
        ):
            self._observe(chunk)
            yield chunk

    def _observe(self, chunk: AgentChunk) -> None:
        """Capture the meeting-scoped session pointer from ``INIT``/``RESULT`` and
        surface ``TOOL_USE`` calls (§3.5 Tier-1 persistence + §3.4 tool-call out).

        The stale-session replay establishes a NEW session on the retry; its
        ``INIT``/``RESULT`` ``session_id`` overwrites the stale pointer here, so
        :attr:`session_id` always names the live session after a recovery."""
        if chunk.type in ("INIT", "RESULT"):
            sid = chunk.metadata.get("session_id")
            if sid:
                self.session_id = sid
        elif chunk.type == "TOOL_USE":
            self.last_tool_calls.append(
                ToolCall(
                    name=chunk.metadata.get("name", ""),
                    input=dict(chunk.metadata.get("input", {}) or {}),
                    id=chunk.metadata.get("id", ""),
                )
            )


__all__ = [
    "DEFAULT_BEHAVIOR",
    "DEFAULT_COMPACTION_EVERY",
    "StateDigest",
    "ToolCall",
    "WakeEvent",
    "WakeTurn",
]
