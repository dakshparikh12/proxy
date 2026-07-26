"""``live_brain`` — assemble the REAL orchestrator brain onto the live meeting path.

The provisioner (``_assemble_runtime``) builds a :class:`~harness.meeting_runtime.MeetingRuntime`
and a run loop, but before this module it called ``build_run_loop()`` with NO arguments —
so the loop ran with ``_noop_wake`` (Proxy never woke) and a never-addressed predicate
(every line folded to the digest, nothing woke), and the live :class:`AbortController`
the loop minted per wake was threaded into a wake that did nothing. The meeting brain was
HOLLOW: on a real meeting Proxy never woke, "Proxy, quiet" halted nothing, and the minted
abort reached no model loop.

This module is the thin ASSEMBLY that closes those holes — it redefines NONE of the
primitives (the run loop, the wake turn, the abort registry, the name-gate, the turn
controller, the ack reflex are all imported and wired, never rebuilt):

1. **REAL WAKE (§3.2).** :func:`build_wake_turn` builds a real :class:`~harness.wake_turn.WakeTurn`
   for the meeting (provider = the injected :class:`AgentProvider`, defaulting to the real
   ``ClaudeAgentProvider()``; registry = ``behaviors.REGISTRY``; notes_reader = the durable
   ``GET /internal/notes`` read; history_fn = the §3.5 transcript-plane reader).
   :func:`make_wake_adapter` returns the ``async def wake(event, digest)`` the run loop
   drives — it selects the wake-behavior (consistent with the direct-answer-path +
   conversational-behaviors nodes), calls ``wake_turn.wake(..., abort=event.abort)``
   threading the LIVE controller the loop minted all the way to the provider (which polls
   ``.aborted`` to break its SDK loop), and emits the result THROUGH the runtime's gated
   emitter (is_owner fencing, §3.7).

2. **NAME-GATE as ``addressed`` (§3.1).** :func:`make_addressed_predicate` wires the built
   mechanical name-gate (Proxy / @proxy scan) as the loop's front-gate verdict: an addressed
   line wakes the real WakeTurn; an un-addressed line folds to the digest with ZERO agent
   calls (the silent-hour = zero-wakes property stays TRUE — silence is un-addressed).

3. **LIVE BARGE-IN → model-loop cancel (§3.11).** :func:`build_barge_in` constructs the live
   :class:`~transport.turn.TurnController` on the SHARED ``runtime.abort_registry`` (never a
   fresh one) + a :class:`~harness.reflex.BoundaryGatedAck`. :meth:`LiveBrain.quiet` routes
   the live "Proxy, quiet" / whisper-stop trigger to ``controller.quiet(task_key)`` where
   ``task_key`` is the addressed in-flight ask's registry key from the run loop's
   ``_task_keys`` bookkeeping — so barge-in cancels the LIVE model loop, not only the TTS
   (the sub-200ms speech cut stays intact).

4. **MEETING-END → cancel_meeting (§3.11).** The meeting-end teardown already calls
   ``abort_registry.cancel_meeting(meeting_id)`` (in ``MeetingRuntimeRegistry.end_meeting``,
   BEFORE the ordered close) so every in-flight model loop for the meeting is aborted at end
   without reordering freeze→close-pass→destroy→complete-row→teardown. This module leans on
   that; :func:`assemble_live_brain` only guarantees the loop mints controllers that end
   reaches.

The provisioner calls :func:`assemble_live_brain` in ``_assemble_runtime`` (right where the
run loop is built) so the live path carries a real brain, not a hollow spine.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from libs.contracts import (
    CanvasPatch,
    ProxyMessage,
    ResponseChunk,
    VoiceSpeak,
)

from . import behaviors
from .name_gate import NameGate
from .reflex import BoundaryGatedAck
from .run_loop import MeetingEvent
from .wake_turn import DEFAULT_BEHAVIOR, WakeEvent, WakeTurn

#: The behaviors that field a spoken/typed conversational ask — selected mechanically by a
#: small keyword scan over the ask text (§3.4 "selecting a behavior by name IS the branch",
#: D-023). This is capability-surface selection (which behavior's tool subset primes the
#: turn), NOT an action mapping: what to DO inside the turn is the model's call (Law 4). The
#: default (:data:`~harness.wake_turn.DEFAULT_BEHAVIOR` = ``answer-question``) is the
#: direct-answer + dispatch envelope; the conversational behaviors ride the same machinery.
_BEHAVIOR_CUES: tuple[tuple[str, str], ...] = (
    ("catch me up", "catch-me-up"),
    ("catch-me-up", "catch-me-up"),
    ("catch up", "catch-me-up"),
    ("where did we land", "where-are-we"),
    ("where are we", "where-are-we"),
    ("what would you do", "dry-run"),
    ("dry run", "dry-run"),
    ("dry-run", "dry-run"),
    ("how did you get", "show-your-work"),
    ("show your work", "show-your-work"),
    ("show-your-work", "show-your-work"),
    ("what can you do", "capability-answer"),
    ("what are you able", "capability-answer"),
)


def select_behavior(text: str, *, registry: dict[str, Any] | None = None) -> str:
    """Select the wake-behavior for one ask by name (§3.4 / D-023) — never a code branch.

    A light, mechanical keyword scan maps a recognizable conversational cue to its
    registered behavior; everything else takes the default answer/dispatch envelope
    (:data:`~harness.wake_turn.DEFAULT_BEHAVIOR`). This selects the capability SURFACE (the
    behavior's curated tool subset / role) — it never decides WHAT the turn does with the
    ask (that is the model's judgment, Law 4). Only names present in the live behavior
    registry are ever returned, so a cue can never name a behavior Proxy doesn't have.
    """
    reg = registry if registry is not None else behaviors.REGISTRY
    lowered = text.lower()
    for cue, name in _BEHAVIOR_CUES:
        if cue in lowered and name in reg:
            return name
    return DEFAULT_BEHAVIOR if DEFAULT_BEHAVIOR in reg else next(iter(reg), DEFAULT_BEHAVIOR)


def _event_text(payload: Any) -> tuple[str, str]:
    """Extract ``(text, speaker)`` from a transport signal — the verbatim ask (§3.2).

    A spoken ``Transcript`` carries ``words`` + ``speaker``; a ``ChatMessage`` carries
    ``message`` + ``sender``. Pure structural reads (never a per-type dispatch branch on
    the routing decision) — an ambient signal with neither yields an empty ask.
    """
    words = getattr(payload, "words", None)
    if isinstance(words, str) and words:
        return words, str(getattr(payload, "speaker", "") or "")
    message = getattr(payload, "message", None)
    if isinstance(message, str) and message:
        return message, str(getattr(payload, "sender", "") or "")
    return "", ""


def build_wake_turn(
    runtime: Any,
    *,
    provider: Any = None,
    notes_reader: Callable[[str], Awaitable[str]] | None = None,
    history_fn: Callable[[], Awaitable[Any]] | None = None,
    registry: dict[str, Any] | None = None,
    mcp_servers: dict[str, Any] | None = None,
) -> WakeTurn:
    """Build the meeting's ONE real :class:`~harness.wake_turn.WakeTurn` (§3.2).

    * ``provider`` — the injected :class:`~agentkit.Provider` seam; defaults to the real
      :class:`~harness.provider.ClaudeAgentProvider` (the SINGLE Claude Agent SDK call
      site, §3.3). A test injects a fake recording stub — no live Anthropic call.
    * ``registry`` — the wake-behavior seats; defaults to ``behaviors.REGISTRY``.
    * ``notes_reader`` — the durable memory read (``GET /internal/notes``, §11.4); defaults
      to the runtime-backed durable read so the turn grounds in the live notes on demand.
    * ``history_fn`` — the §3.5 transcript-plane reader (the same durable meeting-history
      source ``provisioner._resume_session`` reads) so a recycle replays from it.
    * ``mcp_servers`` — the curated MCP servers whose tools the mounted behaviors advertise;
      when ``None`` (the default) it is BUILT from the runtime's ``code_intel_ctx`` so the
      meeting's ``code_intel`` SDK server is mounted and ``mcp__code_intel__*`` resolves to real
      tools (the seam gap this closes). A repo with no built index yields no server — the turn
      still assembles, just without codebase tools (honest degradation). A caller may inject an
      explicit mapping (a test wiring the fixture code_intel server).

    Constructs NO SDK client itself (the provider seam is the only model talker) and
    redefines nothing — it wires the built ``WakeTurn`` for this meeting.
    """
    if provider is None:
        from .provider import ClaudeAgentProvider

        provider = ClaudeAgentProvider()
    reg = registry if registry is not None else behaviors.REGISTRY
    reader = notes_reader if notes_reader is not None else _durable_notes_reader(runtime)
    servers = mcp_servers if mcp_servers is not None else _build_code_intel_servers(runtime)
    return WakeTurn(
        meeting_id=runtime.header.meeting_id,
        provider=provider,
        registry=reg,
        notes_reader=reader,
        history_fn=history_fn,
        mcp_servers=servers,
    )


def _build_code_intel_servers(runtime: Any) -> dict[str, Any] | None:
    """Build the meeting's ``code_intel`` SDK server from the runtime's resolved context.

    The live seam that mounts the codebase graph onto the wake turn (§11.6 / §12.2): the
    provisioner resolved ``runtime.code_intel_ctx`` (this meeting's tenant graph.db + clone)
    at join; here we build the tenant-scoped in-process ``code_intel`` SDK MCP server from it.
    Returns ``{"code_intel": <server>}`` so the wake turn's ``mcp__code_intel__*`` tools are
    reachable. Fail-closed (Rule 6): no context, an unindexed repo, or any build fault yields
    ``None`` — Proxy still wakes, just without codebase tools this meeting (honest degradation),
    never a crash and never a cross-tenant read (the server is scoped to this meeting's tenant).
    """
    ctx = getattr(runtime, "code_intel_ctx", None)
    if ctx is None:
        return None
    try:
        server = ctx.build_server()
    except Exception:  # noqa: BLE001 - a build fault degrades to no-mount, never crashes the meeting
        return None
    if server is None:
        return None
    return {"code_intel": server}


def _durable_notes_reader(runtime: Any) -> Callable[[str], Awaitable[str]]:
    """The durable notes reader bound to this runtime (``GET /internal/notes``, §11.4).

    Folds ``note_deltas`` server-side through the canonical
    :func:`scribe.notes_reader.read_notes` (never a cache read), rendered to the text the
    wake turn primes on. Best-effort: a missing/empty plane yields ``""`` (the notes ride
    only as the ``notes_ref`` handle) — a notes read must NEVER crash the model loop.
    """
    db = getattr(runtime, "db", None)

    async def _read(meeting_id: str) -> str:
        if db is None:
            return ""
        try:
            from scribe.notes_reader import read_notes

            notes = await read_notes(meeting_id, db=db)
        except Exception:  # noqa: BLE001 — a notes read never crashes the model loop (§3.8)
            return ""
        render = getattr(notes, "render", None)
        if callable(render):
            return str(render())
        return str(notes)

    return _read


def make_addressed_predicate(name_gate: NameGate) -> Callable[[MeetingEvent], bool]:
    """Wire the built name-gate as the loop's ``addressed`` front-gate verdict (§3.1).

    The predicate mechanically scans the event's payload (a ``Transcript`` → the spoken
    name-gate + one disambiguation call on a hit; a ``ChatMessage`` → the ``@proxy`` token,
    no model). An addressed line → ``True`` (the loop wakes the real WakeTurn); an
    un-addressed / ambient line → ``False`` (folded to the digest, zero agent calls). The
    silent-hour = zero-wakes property stays TRUE for real: silence is un-addressed.
    """
    from transport.signals import ChatMessage, Transcript

    def _addressed(event: MeetingEvent) -> bool:
        payload = event.payload
        if isinstance(payload, Transcript):
            return name_gate.on_transcript(payload).wake
        if isinstance(payload, ChatMessage):
            return name_gate.on_chat(payload).wake
        return False

    return _addressed


def make_wake_adapter(
    runtime: Any,
    wake_turn: WakeTurn,
    *,
    select: Callable[[str], str] | None = None,
) -> Callable[[MeetingEvent, dict[str, Any]], Awaitable[None]]:
    """The ``async def wake(event, digest)`` the run loop drives on an addressed event.

    It (1) reads the verbatim ask off the event payload, (2) selects the wake-behavior by
    name (§3.4 / D-023), (3) runs the real :meth:`WakeTurn.wake` — threading the LIVE
    controller ``event.abort`` (the one the loop minted) all the way to the provider, which
    polls ``.aborted`` to break its SDK loop (§3.11) — and (4) drives the **pure-rendering
    channel projector** (``transport.projector.ChannelProjector``, Doc 08 §4.5) over that
    delta stream and emits each projected render frame THROUGH the runtime's gated emitter
    (is_owner fencing, §3.7): a fenced-out harness reaches the wire zero times.

    The projector is the SOLE renderer on this live path — the same pure mapping the
    transport carrier drives — so the live product obeys the projector's own rendering law:
    only an explicit delivery tool (``speak`` / ``send_chat`` / ``show_screen``) reaches a
    human, and raw ``TEXT`` (the model's reasoning) / ``INIT`` / ``RESULT`` / ``ERROR``
    project NOTHING (CANONICAL §12.3 — the wake-turn delivery tools are the sole delivery
    authority; there is no bare-TEXT speak). The situation→action mapping stays in the
    model's turn — this adapter owns only the plumbing (Law 4).
    """
    from transport.projector import ChannelProjector

    selector = select if select is not None else select_behavior

    async def _wake(event: MeetingEvent, digest: dict[str, Any]) -> None:
        text, speaker = _event_text(event.payload)
        behavior = selector(text)
        wake_event = WakeEvent(text=text, speaker=speaker)
        projector = ChannelProjector()
        emitter = event.emitter
        # Thread the LIVE controller (event.abort) DOWN to the provider — it polls
        # ``.aborted`` and breaks its SDK loop on quiet / meeting-end / timeout (§3.11).
        # Each chunk is projected ONCE (the delta stream is already ``stream_deltas``
        # output — the projector NEVER re-runs it, CANONICAL §11.3) and every render frame
        # is dispatched to its gated emit-frontier verb the instant it is produced, so a
        # streamed answer reaches the surfaces as it arrives (not batched at turn end).
        async for chunk in wake_turn.wake(
            wake_event, read_notes=True, abort=event.abort, behavior=behavior
        ):
            if emitter is None:
                continue
            for frame in projector.project(chunk):
                _emit_frame(emitter, frame)  # gated on is_owner (§3.7) — a zombie emits nothing

    return _wake


def _emit_frame(emitter: Any, frame: ProxyMessage) -> None:
    """Dispatch ONE projected render frame to its gated emit-frontier verb (§12.3 / §3.7).

    The projector chose the channel by mapping the model's delivery-tool call; this routes
    the frame to the matching gated verb (``speak`` / ``send_chat`` / ``show_screen``) —
    the SOLE outward delivery authority, each fenced on ``is_owner``. A non-delivery render
    frame (e.g. a ``ToolStart`` tile "working…" line) is a status render with no delivery
    verb on this seam; it is skipped here (the tile surface is driven by the render carrier,
    not the emit frontier). The frame's own payload is passed so the wire carries the exact
    delivered text/artifact the projector rendered — never a re-derived string.
    """
    if isinstance(frame, VoiceSpeak):
        emitter.speak(frame.text)
    elif isinstance(frame, ResponseChunk):
        emitter.send_chat(frame.chunk)
    elif isinstance(frame, CanvasPatch):
        emitter.show_screen(frame.patch)


@dataclass
class LiveBrain:
    """The assembled live brain — the wake turn, the name-gate, and the barge-in seam.

    Constructed by :func:`assemble_live_brain` and stashed on the runtime so the live VAD
    "Proxy, quiet" / whisper-stop trigger can reach :meth:`quiet`, which cancels the
    addressed in-flight model loop by looking its registry key up in the run loop's
    ``_task_keys`` bookkeeping — the model loop halts, not just the TTS.
    """

    runtime: Any
    wake_turn: WakeTurn
    controller: Any
    ack: BoundaryGatedAck

    async def quiet(self, ask_id: str | None = None) -> None:
        """"Proxy, quiet" / whisper-stop: cut speech AND halt the addressed model loop (§3.11).

        The speech cut is the intact sub-200ms turn-core path (never replaced). The
        model-loop kill uses the run loop's ``_task_keys`` to resolve the addressed ask's
        registry key (``meeting_id|ask_id``) and cancels THAT controller on the shared
        registry — so the model loop stops, not just the mouth. ``ask_id`` None cuts speech
        only (a quiet with no addressed in-flight turn — still a valid stop).
        """
        task_key = self._task_key_for(ask_id)
        await self.ack.quiet(task_key)

    async def on_vad(self, frame: Any, *, ask_id: str | None = None) -> None:
        """Route a live VAD "Proxy, quiet" / whisper-stop frame to the model-loop cancel.

        The live barge-in trigger: a human whisper-"stop" / "Proxy, quiet" onset both cuts
        the in-flight TTS (sub-200ms) and halts the addressed in-flight model loop. The
        controller's own ``on_vad_frame`` drives the speaking/barge-in FSM; this adds the
        §3.11 model-loop kill keyed off the addressed ask.
        """
        await self.controller.on_vad_frame(frame)
        await self.quiet(ask_id)

    def _task_key_for(self, ask_id: str | None) -> str | None:
        """The registry key of the addressed ask's live controller (run loop bookkeeping).

        Reads the run loop's ``_task_keys`` (ask_id → ``meeting_id|ask_id``) so quiet
        cancels the SAME controller the wake is running under. ``None`` when the ask has no
        in-flight turn — quiet then cuts speech only.
        """
        loop = getattr(self.runtime, "run_loop", None)
        if loop is None or ask_id is None:
            return None
        key = loop._task_keys.get(ask_id)
        return str(key) if key is not None else None


def build_barge_in(
    runtime: Any, *, tts: Any = None, sink: Any = None
) -> tuple[Any, BoundaryGatedAck]:
    """Construct the live :class:`TurnController` (shared registry) + :class:`BoundaryGatedAck`.

    The controller is built on ``runtime.abort_registry`` — the SHARED §11.9 registry the
    run loop mints per-wake controllers through, NOT a fresh one — so ``controller.quiet``
    cancels the very controller a wake is running under (the model-loop kill), while the
    sub-200ms TTS cut stays intact. ``tts`` / ``sink`` are the media seams: they default to
    no-audio placeholders (the real Cartesia synth + Output-Media sink are wired in a later
    media pass), and are injectable so a caller can drive the speech cut with a real synth.
    The abort discipline is what this seam proves live.
    """
    from transport.turn import TurnController

    class _NullTTS:
        """A no-audio TTS placeholder (structural ``TTSProvider``) — the abort/boundary
        discipline is what is live here; the real Cartesia synth is a later media pass."""

        async def synthesize(self, text: str) -> AsyncIterator[bytes]:
            return
            yield b""  # pragma: no cover - a generator that yields nothing

    class _NullSink:
        """A no-audio Output-Media placeholder (structural ``OutputMediaSink``)."""

        async def write_audio(self, chunk: bytes) -> None:
            return None

        async def flush(self) -> None:
            return None

    if runtime.abort_registry is None:
        from agentkit.abort import AbortRegistry

        runtime.abort_registry = AbortRegistry()
    controller = TurnController(
        tts if tts is not None else _NullTTS(),
        sink if sink is not None else _NullSink(),
        abort=runtime.abort_registry,
    )
    ack = BoundaryGatedAck(controller)
    return controller, ack


def assemble_live_brain(
    runtime: Any,
    *,
    provider: Any = None,
    disambiguate: Callable[[str], bool] | None = None,
    notes_reader: Callable[[str], Awaitable[str]] | None = None,
    history_fn: Callable[[], Awaitable[Any]] | None = None,
    tts: Any = None,
    sink: Any = None,
) -> LiveBrain:
    """Assemble the REAL brain onto ``runtime`` and wire it into the run loop (§3.2/§3.11).

    Closes the two live holes the adversarial verifier found: the run loop is built with a
    REAL wake adapter (not ``_noop_wake``) and the name-gate as the ``addressed`` predicate
    (not never-addressed). Also constructs the live barge-in seam on the SHARED abort
    registry so "Proxy, quiet" halts the model loop. Redefines none of the primitives.

    * ``provider`` — the model seam (defaults to the real Claude provider; a test injects a
      fake recording stub, so this assembles with NO live Anthropic call).
    * ``disambiguate`` — the name-gate's one bounded "addressed to me, or 'proxy server'?"
      call on a spoken hit (§3.1); defaults to accept a spoken hit as addressed (chat
      ``@proxy`` never disambiguates). The real bounded model call is injected by the caller.

    Returns the :class:`LiveBrain` (stashed on the runtime by the provisioner) so the live
    VAD "Proxy, quiet" trigger can reach the model-loop cancel.
    """
    wake_turn = build_wake_turn(
        runtime, provider=provider, notes_reader=notes_reader, history_fn=history_fn
    )
    wake_adapter = make_wake_adapter(runtime, wake_turn)

    disambig = disambiguate if disambiguate is not None else (lambda _line: True)
    name_gate = NameGate(disambiguate=disambig)
    addressed = make_addressed_predicate(name_gate)

    # Build the run loop with the REAL wake + the name-gate front gate (not _noop / never-
    # addressed). ``build_run_loop`` is idempotent (builds once) and shares the meeting's
    # abort registry into the loop, so the controller a wake mints is the one meeting-end /
    # "Proxy, quiet" reach.
    runtime.build_run_loop(wake_turn=wake_adapter, addressed=addressed)

    controller, ack = build_barge_in(runtime, tts=tts, sink=sink)
    brain = LiveBrain(runtime=runtime, wake_turn=wake_turn, controller=controller, ack=ack)
    return brain


__all__ = [
    "LiveBrain",
    "assemble_live_brain",
    "build_barge_in",
    "build_wake_turn",
    "make_addressed_predicate",
    "make_wake_adapter",
    "select_behavior",
]
