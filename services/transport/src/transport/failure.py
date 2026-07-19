"""Failure honesty (§3.7): rejoin-once, the honest gap, mark-lost, voice→chat degrade.

Every failure mode has an honest, non-silent path — Proxy is never both broken *and*
pretending (AC-FAIL-17):

- **Bot drop → rejoin exactly once**, then an honest terminal stop; a second drop after
  the one rejoin is an honest stop, never an unbounded retry (AC-FAIL-01/02/06).
- **On rejoin, state the gap plainly**: the announced interval equals the real
  ``[dropped_ts, rejoined_ts]`` window — never fabricated, padded, or shrunk — and the
  record is never presented as continuous across the drop (AC-FAIL-03/04/05). The gap
  line's chat copy is byte-equal to the spoken line (AC-FAIL-19).
- **Un-transcribed audio is marked lost** (the mark-lost path lives in
  :mod:`transport.hearing`; §3.2 leaves buffer-through on the external BYOK leg NOT ours —
  AC-FAIL-09/11). ``transcript_segments`` default ``pending`` → ``comprehended``; on close
  every still-``pending`` segment is backfilled as a gap, none dropped (AC-FAIL-10).
- **Voice provider down → degrade to chat** with a "voice is down — I'll type" notice; a
  dead engine never leaves Proxy both mute and silent (AC-FAIL-12/13). The notice keeps
  text-copy parity (AC-FAIL-20).

The per-bot outbound limiter/queue (AC-FAIL-14/15/16) lives in :mod:`transport.outbound`
and :mod:`transport.limiter`. Bot-status durability + ``delivery_guid`` dedupe
(AC-FAIL-08) is the doc00 ``webhook_events`` path already driven by
:class:`transport.events.WebhookProcessor`; the ``{connected,dropped,rejoined}`` enum is
enforced at that emit boundary (AC-FAIL-07).
"""
from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .signals import BotStatus

#: The bot-status signal is exactly this enum — no out-of-enum value is ever emitted (AC-FAIL-07).
BOT_STATUS_VALUES: frozenset[str] = frozenset({"connected", "dropped", "rejoined"})


def valid_bot_status(status: str) -> bool:
    """True iff ``status`` is one of the exactly-three bot-status values (AC-FAIL-07)."""
    return status in BOT_STATUS_VALUES


class RecoveryState(enum.Enum):
    """The rejoin-once FSM state."""

    LIVE = "live"
    REJOINING = "rejoining"
    HONEST_STOP = "honest_stop"


@dataclass(frozen=True)
class RejoinOutcome:
    """What the policy did for one status transition — always honest, never silent."""

    state: RecoveryState
    rejoin_attempted: bool
    honest_announcement: str = ""


class RejoinPolicy:
    """Rejoin exactly once on a drop; every terminal state is announced, never silent.

    The auto-rejoin budget is **one** for the meeting. A drop with the budget intact
    issues exactly one rejoin attempt; a rejoin that never reconnects, or a second drop
    after the budget is spent, transitions to an honest stop — never a retry loop, never a
    silent giveup (AC-FAIL-01/02/06).
    """

    def __init__(self, *, rejoin: Callable[[], Awaitable[None]]) -> None:
        self._rejoin = rejoin
        self._state = RecoveryState.LIVE
        self._rejoins_used = 0
        self.rejoin_attempts = 0

    @property
    def state(self) -> RecoveryState:
        return self._state

    async def on_status(self, status: BotStatus) -> RejoinOutcome:
        """Drive the FSM from one bot-status transition."""
        if status.status == "dropped":
            return await self._on_drop()
        if status.status in ("connected", "rejoined"):
            if self._state is RecoveryState.REJOINING:
                self._state = RecoveryState.LIVE  # the one rejoin reconnected
            return RejoinOutcome(state=self._state, rejoin_attempted=False)
        return RejoinOutcome(state=self._state, rejoin_attempted=False)

    async def _on_drop(self) -> RejoinOutcome:
        if self._rejoins_used == 0:
            self._rejoins_used = 1
            self.rejoin_attempts += 1
            self._state = RecoveryState.REJOINING
            await self._rejoin()  # exactly one auto-rejoin attempt
            return RejoinOutcome(state=self._state, rejoin_attempted=True)
        # Budget spent (rejoin failed to hold, or a second drop): honest stop, no loop.
        self._state = RecoveryState.HONEST_STOP
        return RejoinOutcome(
            state=self._state,
            rejoin_attempted=False,
            honest_announcement="I dropped again and can't rejoin — I've stopped; the notes have a gap here.",
        )

    def rejoin_failed(self) -> RejoinOutcome:
        """The single rejoin never reconnected → honest terminal stop (AC-FAIL-02)."""
        self._state = RecoveryState.HONEST_STOP
        return RejoinOutcome(
            state=self._state,
            rejoin_attempted=False,
            honest_announcement="I couldn't rejoin — I've stopped; the notes have a gap here.",
        )


@dataclass(frozen=True)
class Gap:
    """A real disconnect window — the announced interval equals [dropped_ts, rejoined_ts]."""

    dropped_ts: float
    rejoined_ts: float

    def line(self) -> str:
        """The plain, honest gap line spoken on rejoin (AC-FAIL-03/04) — one text, posted
        verbatim to chat too (parity, AC-FAIL-19)."""
        return f"I was disconnected {self.dropped_ts:.0f}-{self.rejoined_ts:.0f} — the notes have a gap there."


class GapAnnouncer:
    """Track the real drop window and produce the honest gap — never pretend continuity."""

    def __init__(self) -> None:
        self._dropped_ts: float | None = None

    def on_drop(self, dropped_ts: float) -> None:
        self._dropped_ts = dropped_ts

    def on_rejoin(self, rejoined_ts: float) -> Gap:
        """The gap spanning exactly the real disconnect window (AC-FAIL-04/05)."""
        start = self._dropped_ts if self._dropped_ts is not None else rejoined_ts
        self._dropped_ts = None
        return Gap(dropped_ts=start, rejoined_ts=rejoined_ts)


class SegmentStore(Protocol):
    """Injected seam over doc00 ``transcript_segments`` (status pending|comprehended|lost)."""

    def pending_ids(self) -> list[str]: ...

    async def mark_comprehended(self, segment_id: str) -> None: ...

    async def backfill_gap(self, segment_id: str) -> None: ...


DEFAULT_SEGMENT_STATUS = "pending"


class SegmentReconciler:
    """`pending` → `comprehended` on transcription; close backfills pending as gap (AC-FAIL-10).

    A segment defaults to ``pending``; a transcribed one flips to ``comprehended``; any
    still-``pending`` segment at close is backfilled as a lost/gap segment so none is
    dropped from the record.
    """

    def __init__(self, store: SegmentStore) -> None:
        self._store = store
        self.backfilled = 0

    async def on_transcribed(self, segment_id: str) -> None:
        await self._store.mark_comprehended(segment_id)

    async def on_close(self) -> int:
        """Backfill every still-pending segment as a gap; return how many were backfilled."""
        for segment_id in self._store.pending_ids():
            await self._store.backfill_gap(segment_id)
            self.backfilled += 1
        return self.backfilled


@dataclass(frozen=True)
class DeliveryResult:
    """The honest outcome of a delivery under a possible voice outage."""

    voice_delivered: bool
    chat_delivered: bool
    notice: str = ""

    @property
    def honest(self) -> bool:
        """Never both broken and silent: at least one channel delivered (AC-FAIL-13/17)."""
        return self.voice_delivered or self.chat_delivered


_VOICE_DOWN_NOTICE = "voice is down — I'll type."


class VoiceChatDegrade:
    """Deliver a line honestly: when voice is down, type it on chat with a notice.

    The line is never dropped for lack of voice (AC-FAIL-12); voice-down and chat-down can
    never co-occur for a line (AC-FAIL-13). The voice-down notice posts as text with the
    same verbatim copy as any spoken line (AC-FAIL-20).
    """

    def __init__(self, *, post_chat: Callable[[str], Awaitable[None]]) -> None:
        self._post_chat = post_chat

    async def deliver(self, text: str, *, voice_up: bool) -> DeliveryResult:
        if voice_up:
            # Normal path: spoken elsewhere; the verbatim chat copy is posted for parity.
            await self._post_chat(text)
            return DeliveryResult(voice_delivered=True, chat_delivered=True)
        # Voice down: type the line + an honest notice (both text, both parity-posted).
        await self._post_chat(text)
        await self._post_chat(_VOICE_DOWN_NOTICE)
        return DeliveryResult(voice_delivered=False, chat_delivered=True, notice=_VOICE_DOWN_NOTICE)
