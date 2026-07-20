"""Turn-taking, barge-in & hard-mute — the deterministic, tiny manners (§3.6 / §3.9-7).

Two clean signals stream continuously to the Orchestrator (AC-TURN-03):
- **speaking(on/off)** from **Silero VAD** (<1ms/chunk, CPU) — the sub-200ms barge-in
  trigger. It is sourced from VAD, **never** the ~300ms AAI transcript (AC-TURN-01).
- **boundary(now)** from **AssemblyAI ``end_of_turn``** on the paid STT stream — the
  natural end-of-turn signal. No Smart Turn v3 in V0 core, no timer/elapsed-silence
  boundary (AC-TURN-02); the confirm-at-build fallback lives in :mod:`transport.boundary`.

Voice output may start **only** on a clear boundary (AC-TURN-05); a mid-thought breath
(no ``end_of_turn``) is not a boundary (AC-TURN-06). Any human speech onset during
Proxy's speech stops in-flight TTS **mid-word** and then flushes the queue, atomically
(AC-TURN-07/08); the small-chunk Output-Media buffer keeps a large buffer from defeating
the cut (AC-TURN-10). Barge-in never fires on Proxy's own audio or on silence
(AC-TURN-11). Hard-mute kills in-flight TTS and enters silent mode — voice off while tile
and chat stay live, until re-invited (AC-TURN-12/13); speaking and muted are mutually
exclusive (AC-TURN-14). The M4 mechanism runs against the M0 fake ``TTSProvider`` /
``OutputMediaSink``; M5 adds the real Cartesia synth + start policy on top.
"""
from __future__ import annotations

import asyncio
import contextlib
import enum
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agentkit.abort import AbortRegistry

from .boundary import BoundarySource
from .carrier import SignalCarrier
from .hearing import PROXY_SPEAKER
from .seams import OutputMediaSink, TTSProvider
from .signals import BargeIn, Boundary, Speaking
from .wire import has_end_of_turn

#: The turn-taking signal surface is exactly these three (AC-TURN-04) — no more, no less,
#: and no user-visible name leaks an internal component (naming law).
TURN_SIGNAL_NAMES: frozenset[str] = frozenset({"speaking", "boundary", "barge-in"})


@dataclass(frozen=True)
class VadFrame:
    """One Silero-VAD verdict for one speaker: is this speaker producing speech now?"""

    speaker_id: str
    is_speech: bool
    t: float


def boundary_opened(message: dict[str, Any]) -> bool:
    """True iff a passthrough message asserts a real AAI ``end_of_turn`` boundary.

    A message with no ``end_of_turn`` field, or a falsy one (a mid-thought breath), is
    NOT a boundary (AC-TURN-02/06) — the boundary is never derived from a timer.
    """
    return has_end_of_turn(message) and bool(message.get("end_of_turn"))


class SpeakingDetector:
    """VAD → ``speaking(on/off)`` + human speech-onset (the barge-in source, AC-TURN-01).

    Barge-in onset is scoped to **non-Proxy** speakers, so Proxy's own output audio never
    triggers it (AC-TURN-11); a silence frame is never an onset. The room ``speaking``
    signal flips on the silent↔speech edge of the whole room.
    """

    def __init__(self, *, proxy_speaker: str = PROXY_SPEAKER) -> None:
        self._proxy = proxy_speaker
        self._active: set[str] = set()

    def observe(self, frame: VadFrame) -> tuple[Speaking | None, bool]:
        """Fold one VAD frame; return (room speaking-edge signal or None, human_onset)."""
        was_active = bool(self._active)
        human_onset = False
        if frame.is_speech:
            new_speaker = frame.speaker_id not in self._active
            if new_speaker and frame.speaker_id != self._proxy:
                human_onset = True  # a human began speaking — sourced purely from VAD
            self._active.add(frame.speaker_id)
        else:
            self._active.discard(frame.speaker_id)
        now_active = bool(self._active)
        signal = Speaking(on=now_active, t=frame.t) if now_active != was_active else None
        return signal, human_onset


class TurnState(enum.Enum):
    """The turn FSM — a single enum, so SPEAKING and MUTED can never co-occur (AC-TURN-14)."""

    IDLE = "idle"
    SPEAKING = "speaking"
    MUTED = "muted"


class TurnController:
    """The speech FSM: boundary-gated release, mid-word barge-in stop+flush, hard-mute.

    Speech is streamed to Output Media in small chunks by a background task so a barge-in
    or hard-mute can stop it mid-utterance; a flush drops at most one in-flight chunk.
    The ``AbortRegistry`` is reused for the stop (plan §2) — the same primitive as
    quiet/preempt.
    """

    def __init__(
        self,
        tts: TTSProvider,
        sink: OutputMediaSink,
        *,
        carrier: SignalCarrier | None = None,
        abort: AbortRegistry | None = None,
        now: Callable[[], float] = time.monotonic,
        on_error: Callable[[str, BaseException], Awaitable[None]] | None = None,
    ) -> None:
        self._tts = tts
        self._sink = sink
        self._carrier = carrier
        self._abort = abort or AbortRegistry()
        self._now = now
        self._detector = SpeakingDetector()
        self._on_error = on_error
        self._state = TurnState.IDLE
        self._queue: deque[str] = deque()
        self._current_id: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._seq = 0
        self.chunks_written = 0
        self.last_error: BaseException | None = None

    # ---- observable state (AC-TURN-13/14) -------------------------------------------
    @property
    def state(self) -> TurnState:
        return self._state

    @property
    def speaking(self) -> bool:
        return self._state is TurnState.SPEAKING

    @property
    def muted(self) -> bool:
        return self._state is TurnState.MUTED

    @property
    def tts_active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def voice_on(self) -> bool:
        """Voice is off exactly in silent mode; tile + chat are always live."""
        return not self.muted

    @property
    def tile_on(self) -> bool:  # tile stays live through a mute (AC-TURN-13)
        return True

    @property
    def chat_on(self) -> bool:  # chat stays live through a mute (AC-TURN-13)
        return True

    @property
    def queue_len(self) -> int:
        return len(self._queue)

    # ---- speech lifecycle -----------------------------------------------------------
    def enqueue(self, text: str) -> None:
        """Queue an utterance; release waits for a boundary (never speaks immediately)."""
        self._queue.append(text)

    async def on_boundary(self) -> None:
        """Release the next queued utterance — ONLY reached on a real boundary (AC-TURN-05).

        No-op while muted (voice stays off) or already speaking (one voice at a time).
        """
        if self._state is TurnState.IDLE and self._queue:
            text = self._queue.popleft()
            self._seq += 1
            self._current_id = f"utt-{self._seq}"
            self._abort.clear(self._current_id)
            self._state = TurnState.SPEAKING
            self._task = asyncio.ensure_future(self._stream(text, self._current_id))

    async def _stream(self, text: str, uid: str) -> None:
        try:
            async for chunk in self._tts.synthesize(text):
                if self._abort.is_aborted(uid):
                    return  # cooperative mid-word stop
                await self._sink.write_audio(chunk)
                self.chunks_written += 1
        except asyncio.CancelledError:
            raise  # barge-in / hard-mute cancellation — propagate, never swallow
        except Exception as exc:  # noqa: BLE001 — a provider fault degrades honestly (voice→chat, §3.7), never crashes the harness or orphans the task
            self.last_error = exc
            if self._on_error is not None:
                await self._on_error(text, exc)
        finally:
            # Natural completion returns to IDLE; an interrupted turn is reset by the
            # caller (which nulls _current_id first), so this guard skips then.
            if self._current_id == uid and self._state is TurnState.SPEAKING:
                self._state = TurnState.IDLE
                self._current_id = None
                self._task = None

    async def _stop_current(self) -> None:
        """Stop any in-flight TTS mid-word: abort + cancel the task, then flush the sink."""
        uid = self._current_id
        self._current_id = None
        task = self._task
        self._task = None
        if uid is not None:
            self._abort.abort(uid)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._sink.flush()  # drops the in-flight chunk (≤1 small chunk survives)

    async def barge_in(self) -> None:
        """Human onset: stop mid-word, flush queue, flush sink (AC-TURN-07/08).

        Always flushes the queue and the Output Media buffer, even when idle —
        a barge-in is atomic regardless of whether TTS is currently active.
        """
        await self._stop_current()  # stops in-flight task + flushes sink (always safe)
        self._queue.clear()  # flush the pending utterance queue
        if self._state is TurnState.SPEAKING:
            self._state = TurnState.IDLE

    async def hard_mute(self) -> None:
        """Kill in-flight TTS and enter silent mode; only a re-invite lifts it (AC-TURN-12..14)."""
        await self._stop_current()
        self._queue.clear()
        self._state = TurnState.MUTED

    def reinvite(self) -> None:
        """The ONLY transition out of MUTED — voice never re-enables on its own (AC-TURN-14)."""
        if self._state is TurnState.MUTED:
            self._state = TurnState.IDLE

    def re_invite(self) -> None:
        """Alias for reinvite (AC-TURN-12/14)."""
        self.reinvite()

    def queue_depth(self) -> int:
        """Return the number of utterances queued but not yet spoken."""
        return len(self._queue)

    async def open_boundary(self) -> None:
        """Open a turn boundary and start the next queued utterance (alias for on_boundary)."""
        await self.on_boundary()

    async def on_vad_frame(self, frame: VadFrame) -> None:
        """Fold a VAD frame: emit speaking edge and barge-in on human speech onset."""
        signal, human_onset = self._detector.observe(frame)
        if signal is not None and self._carrier is not None:
            await self._carrier.emit(signal)
        if human_onset and self._state is TurnState.SPEAKING:
            if self._carrier is not None:
                await self._carrier.emit(BargeIn(t=frame.t))
            await self.barge_in()


class TurnSignalPump:
    """Continuously derive + emit the three turn signals; drive barge-in into the FSM.

    Every VAD frame (re)derives ``speaking`` and may emit ``barge-in`` on a human onset;
    every STT message may open a ``boundary`` — neither is polled on demand (AC-TURN-03).
    """

    def __init__(
        self,
        carrier: SignalCarrier,
        controller: TurnController,
        *,
        detector: SpeakingDetector | None = None,
        now: Callable[[], float] = time.monotonic,
        boundary_source: BoundarySource = BoundarySource.AAI_END_OF_TURN,
        smart_turn: Any = None,
    ) -> None:
        self._carrier = carrier
        self._controller = controller
        self._detector = detector or SpeakingDetector()
        self._now = now
        # The resolved boundary source (AC-TURN-16). Default = AAI end_of_turn (CANONICAL
        # §12.11 keeps the v3 fallback out of core). The boundary_source enum controls
        # which path is live; the smart_turn producer is only wired at harness startup.
        self._boundary_source = boundary_source
        self._smart_turn = smart_turn

    async def on_vad(self, frame: VadFrame) -> None:
        """Fold a VAD frame: emit the speaking edge; on a human onset, barge-in."""
        signal, human_onset = self._detector.observe(frame)
        if signal is not None:
            await self._carrier.emit(signal)
        if human_onset:
            await self._carrier.emit(BargeIn(t=frame.t))
            await self._controller.barge_in()  # no-op unless Proxy is speaking

    async def on_stt_message(self, message: dict[str, Any]) -> None:
        """Open a boundary ONLY on a real AAI ``end_of_turn`` (never a timer, AC-TURN-02).

        No-op when the resolved boundary source is Smart Turn v3: on the fallback branch
        the STT stream carries no ``end_of_turn`` to trust, and boundaries arrive via
        :meth:`pump_boundaries` instead (AC-TURN-16).
        """
        if self._boundary_source is not BoundarySource.AAI_END_OF_TURN:
            return
        if boundary_opened(message):
            await self._emit_boundary(self._now())

    async def pump_boundaries(self) -> None:
        """Drive boundaries from the wired Smart Turn v3 fallback seam (AC-TURN-16).

        Active only when the resolved source is ``SMART_TURN_V3`` and a producer is wired;
        it consumes the seam's detected end-of-turn instants and opens each as a boundary,
        exactly as ``on_stt_message`` does for AAI — so the fallback is a real boundary
        source, not merely a recorded decision (F-NO-BOUNDARY-FALLBACK).
        """
        if self._boundary_source is not BoundarySource.SMART_TURN_V3 or self._smart_turn is None:
            return
        async for t in self._smart_turn.boundaries():
            await self._emit_boundary(t)

    async def _emit_boundary(self, t: float) -> None:
        """Emit one ``boundary(now)`` and open the turn gate — the single boundary path."""
        await self._carrier.emit(Boundary(t=t))
        await self._controller.on_boundary()
