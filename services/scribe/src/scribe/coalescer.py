"""The coalescer (what the Scribe sees) — cuts natural windows from the raw stream.

Raw transcript events are too granular to process word-by-word. The coalescer
buffers the Doc 02 transcript stream and cuts **natural windows** — a speaker turn
or a pause within a turn, or the window cap (``[45s]`` OR ``[~1,200 transcript
tokens]``, whichever is smaller), whichever comes first. Silence costs nothing:
a span with no speech emits no window (and therefore no micro-call downstream).

This module owns only the *physics* of window cutting — buffering, boundary
detection, and the hard cap — before any note extraction. It operates on raw
transcript segments, so it deliberately depends on nothing from the note schema.

Design notes tying to the spec (03-MEETING-UNDERSTANDING §3.1):

* A **speaker turn** boundary cuts a window (rapid multi-speaker exchange chunks
  per turn so attribution stays clean — one speaker per window).
* A **pause within a turn** (VAD gap with no speaker change) also cuts a window,
  so a long monologue chunks on its pauses instead of running to the cap.
* The **cap** (45s OR 1,200 tokens) is a hard bound: a segment run that would
  cross the cap is cut at the cap on a turn boundary and the remainder rolls into
  the next window — the window never widens past the cap to catch up.
* **Silence** (a span the stream reports as non-speech) produces no window.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

# --- Physics constants (the hard cap). Only physics/pipes live in code (Law 4). ---
WINDOW_TIME_CAP_S: float = 45.0
"""Hard duration cap for one window handed to the Scribe (§3.1)."""

WINDOW_TOKEN_CAP: int = 1_200
"""Hard transcript-token cap for one window handed to the Scribe (§3.1)."""

PAUSE_GAP_S: float = 0.75
"""A within-turn silent gap at least this long is a pause boundary (chunks a monologue)."""


class BoundaryType(str, Enum):
    """Why a window was cut here — the observable a simulation-trace oracle asserts on."""

    SPEAKER_TURN = "speaker_turn"
    PAUSE_WITHIN_TURN = "pause_within_turn"
    TIME_CAP = "time_cap_60s"  # spec's cap label; the enforced value is WINDOW_TIME_CAP_S
    TOKEN_CAP = "token_cap"
    STREAM_END = "stream_end"


@dataclass(frozen=True)
class TranscriptSegment:
    """One granular speech unit from the Doc 02 stream (raw, pre-extraction).

    ``is_speech=False`` marks a silence/VAD-off span: it carries a time range so
    the buffer can measure gaps, but it contributes no tokens and no window.
    """

    speaker: str
    text: str
    start_s: float
    end_s: float
    token_count: int
    is_speech: bool = True

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class ChatMessage:
    """A meeting-chat message; folded into the window whose span contains its ts."""

    sender: str
    text: str
    ts_s: float


@dataclass(frozen=True)
class Window:
    """A coalesced window handed to the Scribe. Carries everything from its span."""

    segments: tuple[TranscriptSegment, ...]
    boundary_type: BoundaryType
    chat_messages: tuple[ChatMessage, ...] = ()

    @property
    def start_s(self) -> float:
        return self.segments[0].start_s

    @property
    def end_s(self) -> float:
        return self.segments[-1].end_s

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def token_count(self) -> int:
        return sum(s.token_count for s in self.segments)

    @property
    def speakers(self) -> tuple[str, ...]:
        """Distinct speakers who spoke in this window, in first-seen order."""
        seen: list[str] = []
        for s in self.segments:
            if s.speaker not in seen:
                seen.append(s.speaker)
        return tuple(seen)

    @property
    def speaker_count(self) -> int:
        return len(self.speakers)


@dataclass
class _Buffer:
    """Mutable accumulation state between emitted windows."""

    segments: list[TranscriptSegment] = field(default_factory=list)

    @property
    def token_count(self) -> int:
        return sum(s.token_count for s in self.segments)

    @property
    def start_s(self) -> float:
        return self.segments[0].start_s

    @property
    def is_empty(self) -> bool:
        return not self.segments


def _split_segment_at_token_cap(
    seg: TranscriptSegment, tokens_allowed: int
) -> tuple[TranscriptSegment, TranscriptSegment]:
    """Split ONE segment so the head fits ``tokens_allowed`` tokens; tail rolls on.

    Time is apportioned by token fraction so the head's duration is proportional to
    the tokens kept — the remainder (tail) carries the rest of the tokens and time.
    Used only when a single segment alone would blow the token cap with no earlier
    turn boundary to cut on (the cut is still at a boundary: the split point).
    """
    if tokens_allowed <= 0 or tokens_allowed >= seg.token_count:
        raise ValueError("token split point must be strictly inside the segment")
    frac = tokens_allowed / seg.token_count
    mid_s = seg.start_s + seg.duration_s * frac
    head = replace(seg, end_s=mid_s, token_count=tokens_allowed)
    tail = replace(
        seg, start_s=mid_s, token_count=seg.token_count - tokens_allowed
    )
    return head, tail


class Coalescer:
    """Buffers the raw stream and yields ordered windows on natural/cap boundaries.

    Feed segments in stream order with :meth:`push`; feed chat with :meth:`push_chat`;
    call :meth:`flush` at stream end to emit any partial trailing window. Each call
    returns the windows completed by that input (usually zero or one), preserving
    the order they were formed. This is a pure, deterministic transformation — no
    I/O, no clock, no LLM — so it is fully simulation-testable.
    """

    def __init__(
        self,
        *,
        time_cap_s: float = WINDOW_TIME_CAP_S,
        token_cap: int = WINDOW_TOKEN_CAP,
        pause_gap_s: float = PAUSE_GAP_S,
    ) -> None:
        self._time_cap_s = time_cap_s
        self._token_cap = token_cap
        self._pause_gap_s = pause_gap_s
        self._buf = _Buffer()
        self._chat: list[ChatMessage] = []

    # -- input ---------------------------------------------------------------

    def feed(self, seg: TranscriptSegment) -> list[Window]:
        """Add one stream segment; return any windows that closed as a result."""
        if not seg.is_speech:
            # VAD-off / silence: contributes no tokens and no window. Silence costs
            # nothing (§3.1). A silence span never triggers or extends a window.
            return []

        out: list[Window] = []
        pending: TranscriptSegment | None = seg

        while pending is not None:
            boundary = self._boundary_before(pending)
            if boundary is not None and not self._buf.is_empty:
                out.append(self._cut(boundary))
                # re-evaluate the same pending segment against the now-empty buffer
                continue

            # --- Token cap: never let the window exceed the token bound. ---
            token_room = self._token_cap - self._buf.token_count
            if pending.token_count > token_room:
                if not self._buf.is_empty:
                    # cut on the turn boundary at/before the cap; the tail rolls on
                    # (re-evaluate the same pending against the now-empty buffer).
                    out.append(self._cut(BoundaryType.TOKEN_CAP))
                    continue
                # buffer empty but this one segment alone exceeds the cap: split it
                # at the cap; the head is one window, the tail rolls on. Never widen.
                head, tail = _split_segment_at_token_cap(pending, self._token_cap)
                self._buf.segments.append(head)
                out.append(self._cut(BoundaryType.TOKEN_CAP))
                pending = tail
                continue

            # --- Time cap: never let the window's span exceed the duration bound. ---
            window_start = self._buf.start_s if not self._buf.is_empty else pending.start_s
            if pending.end_s - window_start > self._time_cap_s:
                if not self._buf.is_empty:
                    # There is buffered content already at/near the cap: cut it and
                    # re-evaluate pending against the empty buffer (tail rolls on).
                    out.append(self._cut(BoundaryType.TIME_CAP))
                    continue
                # buffer empty but this one segment alone runs past the cap: split
                # it at exactly the cap; the head is one window, the tail rolls on.
                head_end = window_start + self._time_cap_s
                frac = (head_end - pending.start_s) / pending.duration_s
                tok = min(
                    max(1, round(pending.token_count * frac)),
                    max(1, pending.token_count - 1),
                )
                head = replace(pending, end_s=head_end, token_count=tok)
                tail = replace(
                    pending, start_s=head_end, token_count=pending.token_count - tok
                )
                self._buf.segments.append(head)
                out.append(self._cut(BoundaryType.TIME_CAP))
                pending = tail
                continue

            self._buf.segments.append(pending)
            pending = None

        return out

    def push_chat(self, msg: ChatMessage) -> None:
        """Record a chat message; it is folded into the window whose span holds it."""
        self._chat.append(msg)

    def flush(self) -> list[Window]:
        """Emit any partial trailing window at stream end (nothing buffered → none)."""
        if self._buf.is_empty:
            return []
        return [self._cut(BoundaryType.STREAM_END)]

    # -- boundary detection --------------------------------------------------

    def _boundary_before(self, seg: TranscriptSegment) -> BoundaryType | None:
        """Is there a natural boundary between the buffer's tail and ``seg``?"""
        if self._buf.is_empty:
            return None
        tail = self._buf.segments[-1]
        if seg.speaker != tail.speaker:
            return BoundaryType.SPEAKER_TURN
        gap = seg.start_s - tail.end_s
        if gap >= self._pause_gap_s:
            return BoundaryType.PAUSE_WITHIN_TURN
        return None

    # -- cutting -------------------------------------------------------------

    def _cut(self, boundary: BoundaryType) -> Window:
        """Close the current buffer into a Window with its same-span chat, and reset."""
        segs = tuple(self._buf.segments)
        start_s = segs[0].start_s
        end_s = segs[-1].end_s
        chat_in_span = tuple(m for m in self._chat if start_s <= m.ts_s <= end_s)
        for m in chat_in_span:
            self._chat.remove(m)
        self._buf = _Buffer()
        return Window(
            segments=segs, boundary_type=boundary, chat_messages=chat_in_span
        )


def coalesce(
    segments: list[TranscriptSegment],
    chat: list[ChatMessage] | None = None,
    *,
    time_cap_s: float = WINDOW_TIME_CAP_S,
    token_cap: int = WINDOW_TOKEN_CAP,
    pause_gap_s: float = PAUSE_GAP_S,
) -> list[Window]:
    """Convenience: coalesce a full segment list (with optional chat) into windows.

    A batch wrapper over :class:`Coalescer` for tests and offline replay. Chat is
    pushed up front; each message lands in whichever emitted window's span contains
    its timestamp (never dropped as long as some window covers that instant).
    """
    coalescer = Coalescer(
        time_cap_s=time_cap_s, token_cap=token_cap, pause_gap_s=pause_gap_s
    )
    if chat:
        for msg in chat:
            coalescer.push_chat(msg)
    windows: list[Window] = []
    for seg in segments:
        windows.extend(coalescer.feed(seg))
    windows.extend(coalescer.flush())
    return windows
