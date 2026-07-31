"""The signal surface this layer emits (spec §3.10) — the complete communicate-in list.

    transcript(words, speaker, t) · chat(message, sender, dm?) · roster(join/leave, name)
    · speaking(on/off) · boundary(now) · barge-in(now) · bot-status(connected/dropped/rejoined)
    · meeting-end   (``channel-report`` remains a sealed wire NAME but no longer a signal
    VALUE — DM availability is decided by the agent's ``to_meeting`` tool, not emitted here)

These are **transport-internal frozen dataclasses**, deliberately NOT registered as
client ``ProxyMessage`` types (AC-EVENTS-11 / AC-CHAT-14 / AC-SEAM-11 / AC-XCUT-08):
the client registry closure (``assert_registry_closed``) must stay disjoint from this
surface. The wire-name surface itself (``EMITTED_SIGNAL_NAMES``) is the doc00-sealed
``SIGNAL_SURFACE_EVENTS`` frozenset — which still names ``channel-report`` — plus the
internally-emitted ``chat``; the new system decides DM delivery in the agent (the
``to_meeting`` tool), so no ``channel-report`` VALUE is emitted as a dataclass signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

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
    """A participant present/join/leave with name (§3.1) — powers name-aware responses.

    ``present`` is the initial snapshot leg (each participant already in the room at
    join); ``join``/``leave`` are the live deltas — together the who-is-present triad
    (AC-EVENTS-14/02/03).
    """

    kind: Literal["present", "join", "leave"]
    name: str
    participant_id: str

    def __post_init__(self) -> None:
        # A frozen dataclass does NOT enforce ``Literal`` at runtime; enforce it here so
        # an out-of-surface kind can never enter the stream silently (§3.1 / Law 2).
        if self.kind not in ("present", "join", "leave"):
            raise ValueError(f"invalid roster kind: {self.kind!r}")


@dataclass(frozen=True)
class MeetingMetadata:
    """Meeting title + participant list, passed through to the Orchestrator (§3.1)."""

    title: str
    participants: tuple[str, ...]


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

    def __post_init__(self) -> None:
        # bot-status values are exactly {connected, dropped, rejoined} (§3.10, AC-FAIL-07).
        # A frozen dataclass ignores the ``Literal`` at runtime, so validate explicitly —
        # an unknown status must NOT be accepted silently.
        if self.status not in ("connected", "dropped", "rejoined"):
            raise ValueError(f"invalid bot-status: {self.status!r}")


@dataclass(frozen=True)
class MeetingEnd:
    """Explicit meeting-end signal (§3.1) — never inferred from silence."""

    reason: str


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
]

# The sealed ``SIGNAL_SURFACE_EVENTS`` wire names + the internally-emitted ``chat``.
# Kept in one place so a static oracle can prove disjointness from the client registry
# and that no extra (e.g. a screen-ingestion) signal crept in. ``channel-report`` remains
# a sealed wire name even though no dataclass carries it — DM availability is the agent's
# judgment now, not a transport-emitted signal value.
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
    return "meeting-end"
