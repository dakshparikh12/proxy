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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agentkit.abort import AbortRegistry
from scribe.pipeline import HostBudget
from scribe.prefix import MeetingHeader
from scribe.referent import ReferentCorpus

from .run_loop import MeetingEvent, RunLoop, StandingPipe
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
    operation_handle: Any = None
    # The ONE abort registry (§11.9) this meeting's run loop mints per-wake controllers
    # through, shared with the registry so meeting-end / "Proxy, quiet" reach the LIVE
    # controllers. Defaults to a fresh one so a standalone runtime still wires cleanly.
    abort_registry: Any = None
    # The availability-critical STT-credential refresh seam (§3.8): its cadence and the
    # bound refresh callable, threaded from the registry. The loop runs on its OWN
    # in-process asyncio interval (below) — never the scale-to-zero reconcile cron.
    stt_refresh_fn: Callable[[], Awaitable[None]] = _noop_refresh
    stt_refresh_interval_s: float | None = None
    _scribe: ScribeRuntimeHandle | None = field(default=None, init=False)
    _hearing: Any = field(default=None, init=False)
    _run_loop: RunLoop | None = field(default=None, init=False)
    _stt_refresh: "asyncio.Task[None] | None" = field(default=None, init=False)
    _orchestrator_pipe: StandingPipe | None = field(default=None, init=False)
    _meeting_ended: "asyncio.Event | None" = field(default=None, init=False)
    # The assembled live brain (wake turn + name-gate + barge-in seam), stashed by the
    # provisioner so the live VAD "Proxy, quiet" / whisper-stop trigger reaches the
    # §3.11 model-loop cancel. None until :func:`harness.live_brain.assemble_live_brain`
    # wires it (a bare runtime with no brain still tears down cleanly).
    live_brain: Any = field(default=None, init=False)

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
            )
        if self._hearing is None:
            # Import lazily so the harness imports without transport resolved.
            from transport.hearing import HearingStage

            self._hearing = HearingStage(carrier=self.carrier)
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

    # ── the orchestrator run loop — THE per-meeting asyncio spine (§3.2, D-008) ──
    @property
    def run_loop(self) -> RunLoop | None:
        """The per-meeting :class:`~harness.run_loop.RunLoop`, once built (§3.2)."""
        return self._run_loop

    def build_run_loop(
        self,
        *,
        wake_turn: Any = None,
        addressed: Any = None,
        max_in_flight: int = 5,
    ) -> RunLoop:
        """Construct (once) this meeting's run loop — the RUN block of §3's diagram.

        The loop's single delivery seam is the gated :class:`~harness.emit.Emitter`
        bound to this meeting's ``operation_runs`` handle (§3.7 fencing): every
        wake-turn side-effect reads ``is_owner`` live, so a fenced-out harness (a
        replacement re-claimed the meeting) reaches the wire zero times. ``wake_turn``
        is the ONE generic judgment entry (the model); ``addressed`` is the
        mechanical front-gate verdict (the name-gate). Both are injectable so the
        spine assembles before the SDK session/name-gate are wired in later steps.
        """
        if self._run_loop is None:
            emitter = None
            if self.operation_handle is not None:
                from .emit import Emitter

                emitter = Emitter(handle=self.operation_handle)
            # Share the meeting's ONE abort registry (§11.9) with the run loop so the
            # controller a wake mints (``registry.make(meeting_id|ask_id)``) is the SAME
            # handle meeting-end (``cancel_meeting``) and "Proxy, quiet" (``cancel``) reach.
            if self.abort_registry is None:
                self.abort_registry = AbortRegistry()
            self._run_loop = RunLoop(
                wake_turn=wake_turn,
                addressed=addressed,
                emitter=emitter,
                max_in_flight=max_in_flight,
                registry=self.abort_registry,
                meeting_id=self.header.meeting_id,
            )
        return self._run_loop

    def wire_orchestrator_pipe(self) -> StandingPipe:
        """Wire (synchronously) the transport→orchestrator standing pipe ONCE (§3.2).

        Subscribing to the carrier is done HERE, synchronously, so the pipe is
        registered as a subscriber the instant this returns — before any signal is
        emitted. (A carrier subscription registered lazily inside the pump task
        would miss signals that raced ahead of the task's first scheduling.) The
        pipe forwards **every** emitted signal onto the run loop's ONE queue as a
        :class:`~harness.run_loop.MeetingEvent` and routes each THROUGH the loop —
        PURE forwarding, no decision, no branch, no agent (the routing IS the wake
        turn). Builds a default (silent) loop if none was wired.

        **Idempotent (subscribe-once at join, §3.2).** The pipe — and thus the
        carrier subscription — is created exactly once and cached; a second call
        returns the already-wired pipe rather than registering a second subscriber.
        This is the invariant the provisioner leans on: assembly wires the pipe once
        at join, and launching the loop reuses it (never a per-event re-wire).
        """
        if self._orchestrator_pipe is not None:
            return self._orchestrator_pipe
        loop = self._run_loop if self._run_loop is not None else self.build_run_loop()
        self._meeting_ended = asyncio.Event()

        async def _route(signal: Any) -> None:
            # The ask id keys in-flight bookkeeping (dedupe/attach, correction-inject,
            # detach): a spoken/typed line carries one; ambient signals do not.
            ask_id = self._ask_id_for(signal)
            await loop.route(MeetingEvent(payload=signal, ask_id=ask_id))
            # Meeting end is EXPLICIT (§3.1): the MeetingEnd signal — routed through the
            # loop like everything else, never a per-type dispatch branch on the routing
            # decision — trips the end event so the launched spine returns. Detected by a
            # structural marker (the signal's own class name), not an action mapping.
            if type(signal).__name__ == "MeetingEnd" and self._meeting_ended is not None:
                self._meeting_ended.set()

        # subscribe() registers this consumer's queue synchronously (no await),
        # so the pipe is live before run_orchestrator_loop is even scheduled.
        self._orchestrator_pipe = StandingPipe(
            source=self.carrier.subscribe(), sink=_route
        )
        return self._orchestrator_pipe

    async def run_orchestrator_loop(self) -> None:
        """Run the transport→orchestrator standing pipe until the carrier closes (§3.2).

        The RUN-block spine for one meeting: it forwards carrier signals onto the run
        loop, routing each through the loop. Runs until meeting end closes the carrier
        and drains the subscriber. A silent meeting is just this pipe forwarding
        ambient signals while the loop makes zero wake turns. Reuses the pipe wired at
        join (subscribe-once) rather than opening a second subscription.
        """
        pipe = self.wire_orchestrator_pipe()
        await pipe.run()

    async def run_until_meeting_end(self) -> None:
        """Launch the run-loop spine; return when the explicit MeetingEnd signal lands.

        The provisioner's launch seam (§3.2 RUN block). Runs the transport→orchestrator
        pipe (wired ONCE at join) as a task so every carrier signal routes THROUGH the
        loop, and returns the instant a ``MeetingEnd`` signal has routed through — end is
        EXPLICIT (§3.1), never inferred from silence. On return the carrier is closed and
        the pump cancelled so both carrier subscribers (Scribe + orchestrator) drain; the
        caller then runs the ordered close/teardown.
        """
        pipe = self.wire_orchestrator_pipe()
        ended = self._meeting_ended
        pump = asyncio.ensure_future(pipe.run())
        try:
            if ended is not None:
                await ended.wait()
        finally:
            # Close the carrier + stop the pump so the pipe's async-for drains and both
            # carrier subscribers (Scribe consumer + this pipe) terminate cleanly.
            close = getattr(self.carrier, "close", None)
            if close is not None:
                close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump

    @staticmethod
    def _ask_id_for(signal: Any) -> str | None:
        """The in-flight ask id for a signal — the verbatim ask text, else None.

        Pure bookkeeping (§3.15): a spoken transcript / chat line carries its words
        as the ask identity so a duplicate of an in-flight ask attaches to it; an
        ambient signal (boundary, speaking, roster, heartbeat) has none. This reads
        a structural attribute — it is NOT a per-type dispatch branch (the routing
        decision stays the front-gate verdict inside the loop).
        """
        words = getattr(signal, "words", None) or getattr(signal, "message", None)
        return str(words) if isinstance(words, str) and words else None

    async def _drain(self) -> None:
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

            await self.carrier.emit(MeetingEnd(reason="call_ended"))
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(self._scribe.wait(), timeout=_DRAIN_TIMEOUT_S)

    async def run_close(
        self,
        close_config: CloseConfig,
        *,
        teardown: Callable[[], Awaitable[None]] | None = None,
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
        await self._drain()
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
        # The ONE abort registry (§11.9) shared across every meeting's run loop, so
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
    ) -> MeetingRuntime:
        """Create + start the runtime for one meeting; return the existing one on repeat.

        ``referent_corpus`` is the meeting's code index (overview areas + per-repo
        ``graph_nodes``): when supplied it flows to the applier so each marked referent
        binds to a real code node. Absent, referents stay honestly named-but-unbound.
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
            stt_refresh_fn=self._stt_refresh_fn,
            stt_refresh_interval_s=self._stt_refresh_interval_s,
            abort_registry=self._abort_registry,
        )
        runtime.start()
        self._runtimes[header.meeting_id] = runtime
        return runtime

    def get(self, meeting_id: str) -> MeetingRuntime | None:
        return self._runtimes.get(meeting_id)

    async def end_meeting(self, meeting_id: str) -> None:
        """Run the close pass, THEN stop + drop a meeting's runtime (meeting end).

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

                await run_ordered_close(runtime, self._close_config)
            else:
                # No close config bound: still signal meeting end + drain the serial
                # consumer so the trailing window lands in the ledger BEFORE teardown,
                # then release the engine. (run_close does this drain itself.)
                await runtime._drain()
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
