"""``meeting_runtime`` — the per-meeting in-process runtime the harness owns.

The reactive-workroom spine (SPEC §0/§2/§3). A live meeting is exactly two things:
the **workroom** (a per-meeting E2B sandbox with the repo + the live transcript as
files — the agent's whole workspace) and **one connection to the live meeting** (the
host-side driver that carries whatever the agent chooses to say, over the Recall/
Cartesia edges). There is no ``SignalCarrier``, no Scribe notes pipeline, no
``HearingStage`` — the transcript is fed straight into the workroom and the wake gate
decides when to run a reactive turn.

:class:`MeetingRuntime` is the thin holder the rest of the spine finds by meeting id:
it stashes the workroom, the meeting connection, and the :class:`MeetingSession` that
runs the join-time transcript→wake→respond loop, plus the sandbox keep-warm heartbeat
handle the provisioner owns. :class:`MeetingRuntimeRegistry` is the harness's live-
meeting table (one runtime per meeting id), constructed once at boot and stashed on
``app.state.meeting_runtimes`` so both the join path and the provisioner resolve the
SAME runtime.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .meeting_session import MeetingSession

logger = logging.getLogger(__name__)

# Bound on EACH meeting-end teardown step (drain the in-flight turns, flush + close the
# speak pipe, kill the warm sandbox). Teardown must never deadlock meeting end (§3.8): a
# hung turn, a stuck synth, or a hanging E2B kill is abandoned after this bound and the
# drop still completes. unit: seconds.
TEARDOWN_TIMEOUT_S: float = 30.0

__all__ = [
    "TEARDOWN_TIMEOUT_S",
    "MeetingRuntime",
    "MeetingRuntimeRegistry",
]


@dataclass
class MeetingRuntime:
    """One live meeting's in-process runtime — its workroom + its meeting connection.

    Assembled by the provisioner at join: the ``session`` runs the reactive loop (feed
    each transcript line into the workroom, run a cheap wake gate, and on a wake run the
    reactive turn and respond through the connection). ``workroom`` and ``connection``
    are held so meeting-end teardown can drain in-flight turns and kill the sandbox.

    Everything is ``None`` until the provisioner wires it (an assembly fault degrades to
    a runtime with no workroom — the meeting still boots, it just cannot wake this
    meeting; honest degrade, §3.8 / Rule 6). The ``operation_handle`` binds the claimed
    ``operation_runs`` row's fencing handle so meeting-end completes the right row.
    """

    meeting_id: str
    #: The reactive loop (transcript-in → wake gate → run_ask → to_meeting). ``None`` when
    #: no workroom was assembled (the meeting boots without a brain — honest degrade).
    session: MeetingSession | None = None
    #: The per-meeting workroom (native Claude in the E2B sandbox with the repo). Held so
    #: meeting end can drain + kill the sandbox. ``None`` = no workroom this meeting.
    workroom: Any = None
    #: The host-side meeting connection (Recall/Cartesia edges, creds host-side). Held so
    #: the session responds through it and meeting end can close its speak pipe.
    connection: Any = None
    #: The meeting's speak pipe (text→Cartesia→Output-Media channel), flushed + closed at
    #: meeting end after the session drains. ``None`` when no workroom was assembled.
    speak_pipe: Any = None
    #: The claimed ``operation_runs`` row's fencing handle (bound at join).
    operation_handle: Any = None
    #: The sandbox keep-warm heartbeat task the provisioner spawns on a won claim (a
    #: meeting has no time cap, so the 1h-lifetime sandbox is periodically re-extended).
    #: Cancelled in the meeting-end teardown BEFORE the kill. ``None`` = nothing to warm.
    sandbox_keepwarm: Any = field(default=None)
    #: Lines that arrived BEFORE the session was wired. Registration precedes the ~tens-of-
    #: seconds workroom assembly — and the provision launch itself is triggered by the FIRST
    #: transcript line (the liveness event) — so words that race the join, INCLUDING the very
    #: utterance that provisioned the meeting, land here (bounded) and are flushed in order by
    #: :meth:`wire_session`. Never silently dropped.
    pending_lines: list[tuple[str, str, float, bool]] = field(default_factory=list)

    #: Bound on the pre-wire buffer — far above anything a real join window produces; the
    #: oldest lines drop first if a pathological flood exceeds it.
    _PENDING_CAP: ClassVar[int] = 512

    async def ingest_line(
        self, speaker: str, text: str, *, ts: float = 0.0, is_chat: bool = False
    ) -> None:
        """Feed ONE final transcript/chat line into the reactive loop (the webhook drain seam).

        ``is_chat`` selects the chat wake rule (``@proxy``) vs the voice rule (a spoken
        ``proxy``) — the drain sets it True for a ``participant_events.chat_message``. A line
        before the session is wired is BUFFERED (bounded) and flushed by :meth:`wire_session`
        when assembly completes — never dropped, never a raise on the drain path."""
        if self.session is None:
            self.pending_lines.append((speaker, text, ts, is_chat))
            if len(self.pending_lines) > self._PENDING_CAP:
                del self.pending_lines[0 : len(self.pending_lines) - self._PENDING_CAP]
            return
        await self.session.on_line(speaker, text, ts=ts, is_chat=is_chat)

    async def ingest_partial(self, speaker: str, text: str, *, ts: float = 0.0) -> None:
        """Feed ONE non-final (partial) transcript line for BARGE-IN ONLY (BUG 3, Law 3).

        A partial is the earliest signal a human has started talking (~0.5-1.5s before the final
        line). It exists solely to CUT Proxy's active speech the instant a human interjects — it is
        NOT fed as transcript, never wakes, never provisions. Dropped when no session is wired yet: a
        partial can only barge in on active speech, which cannot exist before the session runs. Never
        raises on the drain path."""
        if self.session is None:
            return
        await self.session.on_partial(speaker, text, ts=ts)

    async def wire_session(self, session: MeetingSession) -> None:
        """Attach the assembled session and flush every buffered pre-wire line, in order.

        The ONE way the provisioner attaches a session: assignment + flush live together so
        no caller can wire a session and strand the buffer. A flush fault on one line is
        logged and the rest still feed (never-raise on the drain path)."""
        self.session = session
        pending, self.pending_lines = self.pending_lines, []
        for speaker, text, ts, is_chat in pending:
            try:
                await session.on_line(speaker, text, ts=ts, is_chat=is_chat)
            except Exception:  # noqa: BLE001 - one bad line never strands the rest
                logger.exception(
                    "pre-wire line flush failed on meeting %s (line dropped, rest continue)",
                    self.meeting_id,
                )


class MeetingRuntimeRegistry:
    """The harness's live-meeting table — one :class:`MeetingRuntime` per meeting id.

    Constructed once at boot and stashed on ``app.state.meeting_runtimes`` so both the
    meeting-join path and the bot provisioner resolve the SAME runtime for a given
    meeting. A plain dict: the reactive-workroom model has no per-host Scribe budget, no
    carrier, no close-pass vendor edges — the runtime IS the workroom + the connection.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._runtimes: dict[str, MeetingRuntime] = {}

    def register(self, runtime: MeetingRuntime) -> MeetingRuntime:
        """Register (or return the existing) runtime for a meeting id (idempotent)."""
        existing = self._runtimes.get(runtime.meeting_id)
        if existing is not None:
            return existing
        self._runtimes[runtime.meeting_id] = runtime
        return runtime

    def get(self, meeting_id: str) -> MeetingRuntime | None:
        return self._runtimes.get(meeting_id)

    async def end_meeting(
        self, meeting_id: str, *, reason: str = "call_ended", timeout_s: float | None = None
    ) -> None:
        """Drain in-flight turns + tear the workroom down, then drop the runtime.

        The ordered meeting-end teardown (§3.8 — bounded, never-deadlock): cancel the
        sandbox keep-warm heartbeat FIRST (so no beat re-extends a sandbox mid-teardown) →
        drain the session's in-flight reactive turns → flush + close the speak pipe → kill
        the sandbox (the workroom owns its handle) → drop the Output-Media channel. Every
        step is best-effort AND wall-clock bounded (``asyncio.wait_for`` on the same
        ``TEARDOWN_TIMEOUT_S`` bound), so a hung turn / stuck synth / wedged E2B kill is
        abandoned after the bound rather than blocking the drop behind it. Idempotent: a
        second end is a no-op. ``timeout_s`` overrides the module bound for tests."""
        runtime = self._runtimes.pop(meeting_id, None)
        if runtime is None:
            return
        _ = reason  # the workroom flow has no payload-derived close pass; kept for parity
        bound = timeout_s if timeout_s is not None else TEARDOWN_TIMEOUT_S
        # Stop the keep-warm heartbeat FIRST so no beat re-extends a sandbox mid-teardown.
        keepwarm = runtime.sandbox_keepwarm
        if keepwarm is not None:
            keepwarm.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await keepwarm
        if runtime.session is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(runtime.session.drain(), timeout=bound)
        pipe = runtime.speak_pipe
        aclose = getattr(pipe, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(aclose(), timeout=bound)
        if runtime.workroom is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(runtime.workroom.teardown(), timeout=bound)
        # Drop the meeting's Output-Media audio channel (the speak pipe wrote into it) so
        # the per-meeting channel registry never leaks past meeting end.
        with contextlib.suppress(Exception):
            from in_meeting import output_media

            output_media.close_channel(meeting_id)
