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
from dataclasses import dataclass, field
from typing import Any

from scribe.pipeline import HostBudget
from scribe.prefix import MeetingHeader
from scribe.referent import ReferentCorpus

from .scribe_runtime import (
    CloseConfig,
    ScribeRuntimeHandle,
    run_meeting_close,
    start_meeting_scribe,
)

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
    _scribe: ScribeRuntimeHandle | None = field(default=None, init=False)
    _hearing: Any = field(default=None, init=False)

    def start(self) -> ScribeRuntimeHandle:
        """Launch the live notes engine on this meeting's carrier (idempotent).

        Also constructs the transport-side ``HearingStage`` bound to the SAME carrier —
        the production emit end — so live transcript passthrough fed to
        :meth:`ingest_transcript` fans onto the stream the Scribe consumes. The Scribe
        subscribe end is registered FIRST (``start_meeting_scribe`` subscribes
        synchronously), so no early transcript is dropped on the floor.
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
        return self._scribe

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

    async def _drain(self) -> None:
        """Wait for the serial consumer to drain on meeting end (bounded).

        The transport MeetingEnd signal pushes the None sentinel through the pump so
        the consumer drains and the ledger is complete before the close pass folds
        it. A consumer that does not drain within the bound is left to be cancelled
        by teardown — the close still runs off the durable note_deltas committed so
        far (§3.8: a stuck path degrades honestly, never deadlocks meeting end).
        """
        if self._scribe is None:
            return
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(self._scribe.wait(), timeout=_DRAIN_TIMEOUT_S)

    async def run_close(self, close_config: CloseConfig) -> Any:
        """Drain the consumer, then run the ordered close pass BEFORE teardown.

        This is the wired meeting-end deliverable: the consumer drains, the durable
        ledger is folded + reduced through the strong-model close, the permanent
        markdown notes are written to GCS create-only, the chat link is posted, and
        ONLY THEN is the runtime torn down (``aclose`` IS the close pass's teardown
        step, so the mandatory render->GCS->chat->teardown order is preserved).
        """
        await self._drain()
        return await run_meeting_close(
            self.header, self.db, close_config, teardown=self.aclose
        )

    async def aclose(self) -> None:
        """Tear the notes engine down and close the carrier (host teardown)."""
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
    ) -> None:
        self._db = db
        self._host_budget = HostBudget(limit=host_inflight)
        self._runtimes: dict[str, MeetingRuntime] = {}
        # The close-pass vendor edges (GCS bucket + chat poster + Sonnet caller).
        # Bound at boot; when absent, meeting end still tears the runtime down but
        # cannot produce the permanent record — so the wiring supplies it.
        self._close_config = close_config

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
        teardown. The runtime is dropped from the table regardless, so a close-pass
        failure (surfaced for a human to observe, §3.8) never leaks the runtime.
        """
        runtime = self._runtimes.pop(meeting_id, None)
        if runtime is None:
            return
        try:
            if self._close_config is not None:
                await runtime.run_close(self._close_config)
            else:
                await runtime.aclose()
        finally:
            # run_close's teardown IS aclose; a no-notes/empty-ledger close returns
            # without tearing down, and any close failure must still release the
            # engine — so ensure the runtime is closed exactly once here too.
            await runtime.aclose()


__all__ = [
    "DEFAULT_HOST_INFLIGHT",
    "MeetingRuntime",
    "MeetingRuntimeRegistry",
]
