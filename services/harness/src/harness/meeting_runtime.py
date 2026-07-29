"""``meeting_runtime`` — the per-meeting in-process assembly the harness owns.

Transport (Doc 02) and Scribe (Doc 03) are in-process packages hosted by the ONE
harness process; there is no separate network service for either. A live meeting
gets ONE ``SignalCarrier`` (the in-process fan-out — no bus, no socket) on which
transport emits its signal surface and to which the notes engine subscribes.

This is the assembly point the rest of the codebase's docstrings name: it wires
the carrier to the live notes engine so a real meeting maintains the ledger. On
:func:`MeetingRuntime.start` it launches the Scribe serial consumer
(``harness.scribe_runtime.start_meeting_scribe``) bound to the meeting's carrier;
on :func:`MeetingRuntime.aclose` it tears the engine down. The Recall-bot launch
(wired in a later doc's provisioner) shares this same carrier — transport's emit
seam and the notes engine's subscribe are two ends of the one in-process stream.

The registry is stashed on ``app.state`` at boot so the meeting-join path (and the
provisioner) constructs exactly one runtime per meeting and can find it again.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agentkit.abort import AbortRegistry
from scribe.pipeline import HostBudget
from scribe.prefix import MeetingHeader
from scribe.referent import ReferentCorpus

from .scribe_runtime import (
    CloseConfig,
    ScribeRuntimeHandle,
    run_meeting_close,
    start_meeting_scribe,
)
from .stt import _noop_refresh, refresh_stt_credentials

# Bound on waiting for the serial Scribe consumer to DRAIN on meeting end before
# the close pass folds the ledger. The MeetingEnd signal pushes the None sentinel
# and the consumer drains; this cap keeps a hung consumer from blocking teardown
# forever (a genuinely stuck consumer is cancelled and the close still runs off the
# durable note_deltas already committed). unit: seconds.
_DRAIN_TIMEOUT_S: float = 30.0

# Per-host cap on concurrent Scribe micro-calls (§3.1) — one shared semaphore so a
# busy meeting cannot starve another on the same host. A physics bound, not policy.
DEFAULT_HOST_INFLIGHT: int = 8


@dataclass
class MeetingRuntime:
    """One live meeting's in-process runtime — its carrier + its notes engine.

    The runtime owns BOTH ends of the one in-process stream: transport's emit end
    (:attr:`_hearing`, a real ``HearingStage`` bound to :attr:`carrier`) and the notes
    engine's subscribe end (the Scribe consumer, also on :attr:`carrier`). The live
    meeting-join path feeds real Recall real-time transcript passthrough messages into
    :meth:`ingest_transcript`; they fan onto the carrier and flow
    transport->carrier->coalescer->Scribe into the durable ``note_deltas`` ledger. This
    is the load-bearing bridge between Doc 02's signal surface and Doc 03's consumer
    (gap DOC02-DOC03-TRANSCRIPT-BRIDGE): before it, the Scribe subscribed to an empty
    carrier and the ledger was never populated on a real meeting.
    """

    header: MeetingHeader
    carrier: Any
    db: Any
    host_budget: HostBudget
    referent_corpus: ReferentCorpus | None = None
    # The meeting's resolved code_intel grounding (a ``code_intel.sdk_server.CodeIntelContext``):
    # the tenant's durable per-repo ``graph.db`` index + pinned ``checkout`` clone, resolved
    # once at join from the SAME repo row the referent corpus uses. The live wake turn builds
    # THIS meeting's ``code_intel`` SDK MCP server from it so ``mcp__code_intel__*`` is mounted
    # and Proxy can answer a grounded codebase question (its core premise). ``None`` when the
    # repo is unindexed/unknown — the wake turn then mounts no code_intel server and degrades
    # honestly (it still wakes; it just has no codebase tools this meeting). Never a shared/
    # process-global handle — it is per-meeting-tenant, so one meeting can never read another
    # tenant's volume (isolation triad, Hard Rule 4).
    code_intel_ctx: Any = None
    # The pre-meeting MAP (``index.md``) for this meeting's repo — the durable, verified repo
    # map the pre-meeting system built + stored in Postgres. The live wake turn mounts it as an
    # ORIENTATION prefix on the system prompt so Proxy primes on the codebase mental model before
    # it reads. ``None`` when the repo has no stored map (unindexed) — the wake turn is unaffected.
    map_text: str | None = None
    operation_handle: Any = None
    # The ONE abort registry (§11.9) shared with the registry so meeting-end /
    # "Proxy, quiet" reach the LIVE controllers of this meeting's model turns.
    # Defaults to a fresh one so a standalone runtime still wires cleanly.
    abort_registry: Any = None
    # The availability-critical STT-credential refresh seam (§3.8): its cadence and the
    # bound refresh callable, threaded from the registry. The loop runs on its OWN
    # in-process asyncio interval (below) — never the scale-to-zero reconcile cron.
    stt_refresh_fn: Callable[[], Awaitable[None]] = _noop_refresh
    stt_refresh_interval_s: float | None = None
    # The per-meeting consent hard-gate (§3.1, AC-JOIN-04, Law 3) the LIVE ``HearingStage``
    # reads. Starts CLOSED (fail-closed): until :meth:`grant_consent` opens it — done by the
    # provisioner once the confirmed ``in_call`` join proves the consent notice posted — the
    # live stage DROPS every record (records_before_consent_allowed=0). It is NEVER left as
    # ``can_observe=None`` on the live path (that would default the stage to always-allow and
    # silently observe pre-consent audio, F-RECORD-BEFORE-CONSENT). Defaults to a fresh closed
    # gate so a bare runtime is fail-closed, not always-allow.
    consent_gate: Any = None
    # C-CHATFORMAT — the outbound chat sink the LIVE notes engine drives the §2.4 deterministic
    # chat formatters into: each committed decision/action/correction note-delta is rendered to
    # its ``NoteLine`` and handed here for posting to meeting chat (``start_meeting_scribe`` →
    # the committed-delta applier). ``None`` (the default) → no chat lines are posted this
    # meeting — honest degradation mirroring the NullTTS media placeholder: the deterministic
    # render is race-free + free, but the outbound chat surface (the ChatChannel bound to the
    # transport) is wired by the provisioner/media pass, so a runtime with no chat surface
    # simply posts nothing rather than crashing. A callable taking one rendered ProxyMessage.
    chat_sink: "Callable[[Any], None] | None" = None
    # C-TILE — the outbound tile render sink the wake path drives the §2.2 tile state machine
    # into: when a wake turn's projector emits a work-tool ``ToolStart`` "working…" line, the
    # live brain drives the machine to its ``working`` state and posts the registered
    # ``TileState`` frame here (the render carrier). ``None`` (the default) → the tile isn't
    # driven this meeting — honest degradation mirroring the NullTTS media placeholder (the
    # tile ambience surface is wired by the provisioner/media pass). A callable taking one
    # rendered ``TileState`` ProxyMessage.
    tile_sink: "Callable[[Any], None] | None" = None
    _scribe: ScribeRuntimeHandle | None = field(default=None, init=False)
    _hearing: Any = field(default=None, init=False)
    _stt_refresh: "asyncio.Task[None] | None" = field(default=None, init=False)
    _end_listener: "AsyncIterator[Any] | None" = field(default=None, init=False)
    _meeting_ended: "asyncio.Event | None" = field(default=None, init=False)
    # THE RETIRED BRAIN SEAT: always ``None`` since the cutover — the OLD live brain is
    # deleted and the NEW in-meeting engine (``engine`` below) owns the brain seat. The
    # field survives as the structural negative the cutover proofs pin
    # (``runtime.live_brain is None`` — the old brain must never own the boot path again).
    live_brain: Any = field(default=None, init=False)
    # THE CUTOVER (in-meeting engine on the boot path): the NEW always-on engine
    # (``in_meeting.engine.Engine``) the provisioner assembles at join. Stashed HERE so
    # the webhook drain reaches it by meeting id (``registry.get(meeting_id).engine``)
    # to feed transcript/chat lines — the registry shell stays the one lookup surface,
    # exactly as the old runtime was reachable. ``None`` until the provisioner wires it
    # (the control-plane Scribe-only drain builds runtimes with no engine — those keep
    # the notes plane only).
    engine: Any = field(default=None, init=False)
    # The engine's speak pipe (``in_meeting.speak.SpeakPipe`` — text→Cartesia→Output-Media
    # channel), held so meeting end can flush + close it (``aclose``) after the engine
    # drains. ``None`` when no engine was assembled.
    speak_pipe: Any = field(default=None, init=False)
    # The meeting's warm E2B sandbox handle (provisioned at join, ``in_meeting.sandbox``).
    # The PROVISIONER owns its lifecycle: killed at meeting end in the same teardown that
    # completes the operation row. ``None`` = the meeting runs without sandbox tools (an
    # honest degrade — a provision fault never kills the meeting).
    engine_sandbox: Any = field(default=None, init=False)
    # The per-meeting transport ``WebhookProcessor`` bound to THIS meeting's carrier
    # (C-SIGNALWIRE): the live webhook drain routes roster (present/join/leave), bot-status
    # (connected/dropped/rejoined) and meeting-end webhooks through it so those signals reach
    # the SAME carrier the Scribe + Orchestrator subscribe to — before this binding those
    # producers existed but had NO live caller, so roster/bot-status never reached the live
    # stream. Built once (its dedupe state — present-snapshot-once, meeting-end-once, the name
    # cache — must persist across the meeting's webhooks) and cached here.
    _webhook_processor: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        # Fail-closed by default: a runtime with no explicit consent gate gets a fresh CLOSED
        # one so the live HearingStage drops every record until :meth:`grant_consent` opens it.
        # (Never left None — a None gate would give the live stage can_observe=None = always-allow.)
        if self.consent_gate is None:
            from transport.join import ConsentGate

            self.consent_gate = ConsentGate()

    def grant_consent(self) -> None:
        """Open the consent hard-gate — consent notice confirmed posted (§3.1, AC-JOIN-04).

        Called by the provisioner once the confirmed ``in_call`` join proves the consent
        notice posted (the bot reaches ``in_call`` only after :meth:`JoinSession.join` posted
        the notice as its first observable action). After this the live ``HearingStage`` stops
        dropping records and observation begins; before it, every record is dropped (Law 3).
        """
        self.consent_gate.grant()

    def start(self) -> ScribeRuntimeHandle:
        """Launch the join-time standing-pipe plumbing on this meeting's carrier (§2/§3).

        The standing pipes are wired ONCE here, at join, as pure forwarding with zero
        agent calls (§3.2). This launches:

        * the live notes engine — the Scribe serial consumer subscribed to the ONE
          ``SignalCarrier`` exactly once (audio→STT→transcript→Scribe→material-events);
        * the transport-side ``HearingStage`` bound to the SAME carrier (the production
          emit end) so live transcript passthrough fed to :meth:`ingest_transcript` fans
          onto the stream the Scribe consumes; and
        * the availability-critical STT-credential refresh loop on its OWN in-process
          asyncio interval (§3.8 — NOT the scale-to-zero reconcile cron, because a live
          meeting provably has a warm instance).

        Idempotent: a redelivered ``in_call`` returns the already-wired pipes without a
        second subscription (subscription count stays 1). The Scribe subscribe end is
        registered FIRST (``start_meeting_scribe`` subscribes synchronously), so no early
        transcript is dropped on the floor.
        """
        if self._scribe is None:
            self._scribe = start_meeting_scribe(
                self.header,
                self.carrier,
                self.db,
                host_budget=self.host_budget,
                referent_corpus=self.referent_corpus,
                chat_sink=self.chat_sink,
            )
        if self._hearing is None:
            # Import lazily so the harness imports without transport resolved.
            from transport.hearing import HearingStage

            # Wire the consent hard-gate into the LIVE HearingStage (§3.1, AC-JOIN-04, Law 3):
            # the stage reads ``consent_gate.can_observe`` and DROPS every record until consent
            # is granted (records_before_consent_allowed=0). NEVER can_observe=None on the live
            # path — that defaults the stage to always-allow and silently observes pre-consent
            # audio (F-RECORD-BEFORE-CONSENT).
            self._hearing = HearingStage(
                carrier=self.carrier, can_observe=self.consent_gate.can_observe
            )
        if self._stt_refresh is None:
            # The STT-credential refresh runs on its OWN in-process interval task, wired
            # once at join and torn down at meeting end — never on the reconcile cron
            # (§3.8). It is a standing pipe: no agent, no decision, just a constant
            # connection kept warm for as long as the meeting is live.
            self._stt_refresh = asyncio.ensure_future(
                refresh_stt_credentials(
                    self.stt_refresh_fn, interval_s=self.stt_refresh_interval_s
                )
            )
        return self._scribe

    @property
    def stt_refresh_running(self) -> bool:
        """True iff the STT-credential refresh loop task is live (started, not stopped)."""
        return self._stt_refresh is not None and not self._stt_refresh.done()

    async def ingest_transcript(self, msg: dict[str, Any]) -> None:
        """Fan ONE real Recall real-time transcript passthrough message onto the carrier.

        The production emit end of the bridge: the harness webhook drain hands each live
        ``transcript`` passthrough body here; the runtime's ``HearingStage`` parses it
        with the fail-loud confirmed-wire parser and emits the resulting ``Transcript``
        signal onto :attr:`carrier` — the SAME stream the Scribe subscribes to. Ensures
        the stage is bound (a transcript that races the runtime start still finds one).
        """
        if self._hearing is None:
            self.start()
        await self._hearing.ingest_wire_transcript(msg)

    def webhook_processor(self) -> Any:
        """The per-meeting transport ``WebhookProcessor`` bound to THIS carrier (C-SIGNALWIRE).

        Built once and cached so its once-only dedupe state (the initial present-set snapshot,
        the exactly-once meeting-end guard, the participant name cache) persists across the
        meeting's roster/bot-status/meeting-end webhooks. It emits onto :attr:`carrier` — the
        SAME in-process stream the Scribe consumer and the meeting-end listener subscribe to — so
        every one of the nine §3.10 signals reaches its live consumer through the ONE binding.
        The drain already persisted the row durably, so callers drive the pure ``_emit_for``
        emit step (never a second persist).
        """
        if self._webhook_processor is None:
            from transport.events import WebhookProcessor

            self._webhook_processor = WebhookProcessor(self.carrier)
        return self._webhook_processor

    # ── the meeting-end listener — the ONE spine signal the launch waits on (§3.1) ──
    def wire_meeting_end_listener(self) -> AsyncIterator[Any]:
        """Wire (synchronously) the meeting-end listener ONCE at join (§3.1/§3.2).

        Subscribing to the carrier is done HERE, synchronously, so the listener is
        registered as a subscriber the instant this returns — before any signal is
        emitted. (A carrier subscription registered lazily inside the pump task
        would miss a ``MeetingEnd`` that raced ahead of the task's first
        scheduling.) The listener is PURE forwarding with zero agent involvement:
        it consumes every emitted signal and trips the end event when the explicit
        ``MeetingEnd`` signal lands — detected by a structural marker (the signal's
        own class name), never a per-type action mapping. The brain seat is the NEW
        in-meeting engine (fed by the webhook drain); nothing here routes a wake.

        **Idempotent (subscribe-once at join).** The subscription is created
        exactly once and cached; a second call returns the already-wired source
        rather than registering a second subscriber. This is the invariant the
        provisioner leans on: assembly wires the listener once at join, and
        launching the spine reuses it (never a per-event re-wire).
        """
        if self._end_listener is not None:
            return self._end_listener
        self._meeting_ended = asyncio.Event()
        # subscribe() registers this consumer's queue synchronously (no await),
        # so the listener is live before run_until_meeting_end is even scheduled.
        self._end_listener = self.carrier.subscribe()
        return self._end_listener

    async def _consume_until_meeting_end(self) -> None:
        """Drain the listener's subscription, tripping the end event on ``MeetingEnd``.

        Meeting end is EXPLICIT (§3.1) — the ``MeetingEnd`` signal on the carrier, never
        inferred from silence. Every other signal is consumed and dropped here (the
        Scribe consumer holds its OWN subscription; the engine is fed by the drain), so
        the subscriber queue never backs up over a long meeting.
        """
        source = self.wire_meeting_end_listener()
        async for signal in source:
            if type(signal).__name__ == "MeetingEnd" and self._meeting_ended is not None:
                self._meeting_ended.set()

    async def run_until_meeting_end(self) -> None:
        """Run the meeting-end listener; return when the explicit MeetingEnd signal lands.

        The provisioner's launch seam. Pumps the listener (wired ONCE at join) as a
        task and returns the instant a ``MeetingEnd`` signal has landed — end is
        EXPLICIT (§3.1), never inferred from silence. On return the carrier is closed
        and the pump cancelled so both carrier subscribers (Scribe + this listener)
        drain; the caller then runs the ordered close/teardown.
        """
        self.wire_meeting_end_listener()
        ended = self._meeting_ended
        pump = asyncio.ensure_future(self._consume_until_meeting_end())
        try:
            if ended is not None:
                await ended.wait()
        finally:
            # Close the carrier + stop the pump so the listener's async-for drains and
            # both carrier subscribers (Scribe consumer + this listener) terminate cleanly.
            close = getattr(self.carrier, "close", None)
            if close is not None:
                close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump

    async def _drain(self, *, reason: str = "call_ended") -> None:
        """Signal meeting end onto the carrier, then wait for the consumer to drain (bounded).

        Meeting end is EXPLICIT, never inferred from silence (§3.1): the runtime emits a
        transport ``MeetingEnd`` signal onto :attr:`carrier` — the SAME stream the pump
        consumes — so the pump flushes the trailing partial window (the last speaker's
        turn, which no mid-stream boundary cut) and pushes the ``None`` sentinel. The
        serial consumer then drains that sentinel and stops, so the ledger is complete
        before the close pass folds it. Without this emit the pump would block forever on
        the carrier (no signal ever tells it the meeting ended) and the trailing window —
        often the ONLY window on a short meeting — would never reach the notes engine.

        A consumer that does not drain within the bound is left to be cancelled by
        teardown — the close still runs off the durable note_deltas committed so far
        (§3.8: a stuck path degrades honestly, never deadlocks meeting end).
        """
        if self._scribe is None:
            return
        # Emit MeetingEnd onto the carrier the pump subscribes to (explicit end, §3.1).
        # Import lazily so the harness imports without transport resolved (same seam the
        # HearingStage bind uses). A carrier already closed/failed must not block teardown,
        # so the emit is best-effort — the bounded wait below still drains what landed.
        with contextlib.suppress(Exception):
            from transport.signals import MeetingEnd

            # Reason is DERIVED from the webhook payload (threaded from ``end_meeting``),
            # never a hard-coded synth string — the C-ENDOFTURN payload-derived meeting-end.
            await self.carrier.emit(MeetingEnd(reason=reason))
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(self._scribe.wait(), timeout=_DRAIN_TIMEOUT_S)

    async def run_close(
        self,
        close_config: CloseConfig,
        *,
        teardown: Callable[[], Awaitable[None]] | None = None,
        reason: str = "call_ended",
    ) -> Any:
        """Drain the consumer (FREEZE), then run the close pass BEFORE teardown.

        This is the wired meeting-end deliverable: the consumer drains — which
        FREEZES the durable ``note_deltas`` ledger (no delta is appended past the
        MeetingEnd sentinel) — the ledger is folded + reduced through the
        strong-model close, the permanent markdown notes are written to GCS
        create-only, the chat link is posted, and ONLY THEN is ``teardown`` run.

        ``teardown`` is the close pass's final step. The ordered close (§3.16) passes
        its ordered TAIL here — destroy-sandbox → complete-harness-row →
        teardown-pipes LAST — so the pipes come down only after the harness row is
        completed. Defaults to :meth:`aclose` (the bare pipe teardown) for callers
        that only need the render->GCS->chat->teardown order.
        """
        await self._drain(reason=reason)
        return await run_meeting_close(
            self.header,
            self.db,
            close_config,
            teardown=teardown if teardown is not None else self.aclose,
        )

    async def aclose(self) -> None:
        """Tear the standing pipes down and close the carrier (host teardown).

        Cancels the availability-critical STT-credential refresh loop (it must not
        outlive the meeting it serves — §3.8) and the Scribe notes engine, then closes
        the carrier. Best-effort and idempotent: a second teardown is a no-op.
        """
        if self._stt_refresh is not None:
            self._stt_refresh.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stt_refresh
            self._stt_refresh = None
        if self._scribe is not None:
            await self._scribe.aclose()
            self._scribe = None
        close = getattr(self.carrier, "close", None)
        if close is not None:
            close()


class MeetingRuntimeRegistry:
    """The harness's live-meeting table — one ``MeetingRuntime`` per meeting id.

    Constructed once at boot and stashed on ``app.state`` so both the meeting-join
    path and the bot provisioner resolve the SAME runtime (and its one carrier) for
    a given meeting. The shared per-host in-flight budget lives here so every
    meeting's Scribe consumer draws from the one semaphore.
    """

    def __init__(
        self,
        db: Any,
        *,
        host_inflight: int = DEFAULT_HOST_INFLIGHT,
        close_config: CloseConfig | None = None,
        stt_refresh_fn: Callable[[], Awaitable[None]] = _noop_refresh,
        stt_refresh_interval_s: float | None = None,
        abort_registry: AbortRegistry | None = None,
    ) -> None:
        self._db = db
        self._host_budget = HostBudget(limit=host_inflight)
        self._runtimes: dict[str, MeetingRuntime] = {}
        # The ONE abort registry (§11.9) shared across every meeting's runtime, so
        # ``end_meeting`` can ``cancel_meeting`` every in-flight model-loop controller of
        # a meeting (AC-CTRL-012). One registry for all meetings is safe: keys are scoped
        # ``meeting_id|task_id``, so ``cancel_meeting`` never touches a sibling meeting.
        self._abort_registry = abort_registry if abort_registry is not None else AbortRegistry()
        # The close-pass vendor edges (GCS bucket + chat poster + Sonnet caller).
        # Bound at boot; when absent, meeting end still tears the runtime down but
        # cannot produce the permanent record — so the wiring supplies it.
        self._close_config = close_config
        # The availability-critical STT-credential refresh seam (§3.8): bound once at
        # boot and handed to every meeting's runtime so each keeps its own in-process
        # refresh interval alive for the life of the meeting (never the reconcile cron).
        self._stt_refresh_fn = stt_refresh_fn
        self._stt_refresh_interval_s = stt_refresh_interval_s

    def start_meeting(
        self,
        header: MeetingHeader,
        carrier: Any,
        *,
        referent_corpus: ReferentCorpus | None = None,
        code_intel_ctx: Any = None,
        map_text: str | None = None,
    ) -> MeetingRuntime:
        """Create + start the runtime for one meeting; return the existing one on repeat.

        ``referent_corpus`` is the meeting's code index (overview areas + per-repo
        ``graph_nodes``): when supplied it flows to the applier so each marked referent
        binds to a real code node. Absent, referents stay honestly named-but-unbound.

        ``code_intel_ctx`` is the meeting's resolved code_intel grounding (the tenant's
        durable ``graph.db`` + pinned ``checkout``): when supplied the live wake turn builds
        THIS meeting's ``code_intel`` SDK server from it so grounded codebase questions can be
        answered. Absent, the wake turn mounts no code_intel server (honest degradation).
        """
        existing = self._runtimes.get(header.meeting_id)
        if existing is not None:
            return existing
        runtime = MeetingRuntime(
            header=header,
            carrier=carrier,
            db=self._db,
            host_budget=self._host_budget,
            referent_corpus=referent_corpus,
            code_intel_ctx=code_intel_ctx,
            map_text=map_text,
            stt_refresh_fn=self._stt_refresh_fn,
            stt_refresh_interval_s=self._stt_refresh_interval_s,
            abort_registry=self._abort_registry,
        )
        runtime.start()
        self._runtimes[header.meeting_id] = runtime
        return runtime

    def get(self, meeting_id: str) -> MeetingRuntime | None:
        return self._runtimes.get(meeting_id)

    async def end_meeting(self, meeting_id: str, *, reason: str = "call_ended") -> None:
        """Run the close pass, THEN stop + drop a meeting's runtime (meeting end).

        ``reason`` is the payload-derived meeting-end cause (C-ENDOFTURN): the live webhook
        handler passes the actual Recall terminal reason so the emitted ``MeetingEnd`` carries
        it rather than a hard-coded ``call_ended``. It defaults to ``call_ended`` for callers
        that end a meeting without a webhook payload (test/teardown paths).

        This is the wired meeting-end path (gap DOC03-CLOSE-PASS-UNWIRED): when a
        close config is bound the runtime drains, folds the ledger, produces the
        permanent markdown notes record (GCS create-only), posts the chat link, and
        ONLY THEN tears down — in that order (the close pass's own teardown step is
        the runtime ``aclose``). Without a close config it falls back to a bare
        teardown — but STILL drains first: meeting end signals the transcript pump so
        the trailing window (often the only window on a short meeting) reaches the
        notes engine and the ledger is complete, then the engine is released. Cancelling
        an un-drained consumer here would drop that trailing window on the floor (the
        very transcript->ledger bridge gap). The runtime is dropped from the table
        regardless, so a close-pass failure (surfaced for a human to observe, §3.8)
        never leaks the runtime.
        """
        runtime = self._runtimes.pop(meeting_id, None)
        if runtime is None:
            return
        # AC-CTRL-012: abort every in-flight model-loop controller of THIS meeting FIRST
        # (§3.11 "meeting-end → cancel everything"). A wake/dispatch still mid-run when the
        # meeting ends is cooperatively halted (the provider breaks its SDK loop) so no
        # model loop survives meeting end burning budget. Scoped ``meeting_id|task_id`` so a
        # sibling meeting is untouched (isolation). Done before the ordered close below —
        # it neither reorders nor blocks freeze→close-pass→destroy→complete-row→teardown.
        self._abort_registry.cancel_meeting(meeting_id)
        try:
            if self._close_config is not None:
                # The §3.16 ordered close over Doc 03's close pass: freeze (drain) →
                # close-pass (notes GCS create-only + chat link) → destroy-sandbox →
                # complete-harness-row → teardown-pipes LAST. run_ordered_close owns
                # the ordered tail so the pipes come down only after the harness row
                # is completed and the sandbox destroyed — nothing reads a torn-down
                # store, and the close is idempotent on re-run (create-only notes).
                from .close import run_ordered_close

                await run_ordered_close(runtime, self._close_config, reason=reason)
            else:
                # No close config bound: still signal meeting end + drain the serial
                # consumer so the trailing window lands in the ledger BEFORE teardown,
                # then release the engine. (run_close does this drain itself.)
                await runtime._drain(reason=reason)
                await runtime.aclose()
        finally:
            # The ordered close's teardown-pipes is aclose; a no-notes/empty-ledger
            # close or any close failure must still release the engine — so ensure the
            # runtime is closed exactly once here too (aclose is idempotent).
            await runtime.aclose()


__all__ = [
    "DEFAULT_HOST_INFLIGHT",
    "MeetingRuntime",
    "MeetingRuntimeRegistry",
]
