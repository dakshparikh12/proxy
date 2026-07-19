"""The signal surface this layer emits (spec §3.10) — the complete communicate-in list.

    transcript(words, speaker, t) · chat(message, sender, dm?) · roster(join/leave, name)
    · speaking(on/off) · boundary(now) · barge-in(now) · bot-status(connected/dropped/rejoined)
    · meeting-end · channel-report(dm_available)

These are **transport-internal frozen dataclasses**, deliberately NOT registered as
client ``ProxyMessage`` types (AC-EVENTS-11 / AC-CHAT-14 / AC-SEAM-11 / AC-XCUT-08):
the client registry closure (``assert_registry_closed``) must stay disjoint from this
surface. Eight of the nine names are the doc00-sealed ``SIGNAL_SURFACE_EVENTS``
frozenset; ``chat`` is the ninth, emitted internally (never a client ``MessageType``).
``ChannelReport`` is imported from ``libs/contracts`` — reused, never re-defined
(AC-CHAT-12 / AC-SEAM-12).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from contracts.channels import ChannelReport
from contracts.registry import SIGNAL_SURFACE_EVENTS


@dataclass(frozen=True)
class Transcript:
    """A speaker-attributed transcript record (§3.2) with ~300ms word latency."""

    words: str
    speaker: str
    t: float
    is_final: bool = True


@dataclass(frozen=True)
class ChatMessage:
    """An inbound/outbound chat line; ``dm`` marks private delivery (§3.4)."""

    message: str
    sender: str
    dm: bool = False


@dataclass(frozen=True)
class RosterEvent:
    """A participant join/leave with name (§3.1) — powers name-aware responses."""

    kind: Literal["join", "leave"]
    name: str
    participant_id: str


@dataclass(frozen=True)
class Speaking:
    """Silero-VAD speech-or-silence (§3.6): ``on`` is the barge-in trigger."""

    on: bool
    t: float


@dataclass(frozen=True)
class Boundary:
    """A natural end-of-turn boundary just opened (AAI ``end_of_turn``, §3.6)."""

    t: float


@dataclass(frozen=True)
class BargeIn:
    """A human speech onset during Proxy's speech — stop TTS mid-word (§3.6)."""

    t: float


@dataclass(frozen=True)
class BotStatus:
    """Recall bot-status transition (§3.7) — drives rejoin + the honest gap line."""

    status: Literal["connected", "dropped", "rejoined"]
    t: float


@dataclass(frozen=True)
class MeetingEnd:
    """Explicit meeting-end signal (§3.1) — never inferred from silence."""

    reason: str


# ``channel-report`` reuses the libs/contracts model verbatim (dm_available: bool).
ChannelReportSignal = ChannelReport

# The complete emitted surface. Any behavior upstream needing a signal not on this
# list has a gap that belongs *here* (§3.10).
Signal = Union[
    Transcript,
    ChatMessage,
    RosterEvent,
    Speaking,
    Boundary,
    BargeIn,
    BotStatus,
    MeetingEnd,
    ChannelReport,
]

# Nine names: the eight sealed ``SIGNAL_SURFACE_EVENTS`` + the internally-emitted
# ``chat``. Kept in one place so a static oracle can prove disjointness from the
# client registry and that no tenth (e.g. a screen-ingestion) signal crept in.
EMITTED_SIGNAL_NAMES: frozenset[str] = SIGNAL_SURFACE_EVENTS | {"chat"}


def signal_name(sig: Signal) -> str:
    """Return the §3.10 wire name for an emitted signal."""
    if isinstance(sig, Transcript):
        return "transcript"
    if isinstance(sig, ChatMessage):
        return "chat"
    if isinstance(sig, RosterEvent):
        return "roster"
    if isinstance(sig, Speaking):
        return "speaking"
    if isinstance(sig, Boundary):
        return "boundary"
    if isinstance(sig, BargeIn):
        return "barge-in"
    if isinstance(sig, BotStatus):
        return "bot-status"
    if isinstance(sig, MeetingEnd):
        return "meeting-end"
    return "channel-report"
