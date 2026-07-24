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

from dataclasses import dataclass, field
from typing import Any

from scribe.pipeline import HostBudget
from scribe.prefix import MeetingHeader
from scribe.referent import ReferentCorpus

from .scribe_runtime import ScribeRuntimeHandle, start_meeting_scribe

# Per-host cap on concurrent Scribe micro-calls (§3.1) — one shared semaphore so a
# busy meeting cannot starve another on the same host. A physics bound, not policy.
DEFAULT_HOST_INFLIGHT: int = 8


@dataclass
class MeetingRuntime:
    """One live meeting's in-process runtime — its carrier + its notes engine."""

    header: MeetingHeader
    carrier: Any
    db: Any
    host_budget: HostBudget
    referent_corpus: ReferentCorpus | None = None
    _scribe: ScribeRuntimeHandle | None = field(default=None, init=False)

    def start(self) -> ScribeRuntimeHandle:
        """Launch the live notes engine on this meeting's carrier (idempotent)."""
        if self._scribe is None:
            self._scribe = start_meeting_scribe(
                self.header,
                self.carrier,
                self.db,
                host_budget=self.host_budget,
                referent_corpus=self.referent_corpus,
            )
        return self._scribe

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

    def __init__(self, db: Any, *, host_inflight: int = DEFAULT_HOST_INFLIGHT) -> None:
        self._db = db
        self._host_budget = HostBudget(limit=host_inflight)
        self._runtimes: dict[str, MeetingRuntime] = {}

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
        """Stop + drop a meeting's runtime (host teardown / meeting end)."""
        runtime = self._runtimes.pop(meeting_id, None)
        if runtime is not None:
            await runtime.aclose()


__all__ = [
    "DEFAULT_HOST_INFLIGHT",
    "MeetingRuntime",
    "MeetingRuntimeRegistry",
]
