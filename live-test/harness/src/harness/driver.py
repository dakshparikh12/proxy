"""The step-driven driver — the operator calls it once per chunk (that IS the pause).

There are NO timeouts/stalls in the loop itself: ``play_chunk`` plays a chunk's
beats and returns; the operator then reads the bundle, grades, fixes, and calls
``replay_chunk`` or the next ``play_chunk``. The pause is simply that control
returns to the operator between chunks.

Gate handling (per beat's ``Gate``):

* ``speak-now``            — synthesize + push to THAT replica's channel now.
* ``don't-address``        — same, but the line carries no wake word (played as-is;
                             the transcript already omits "Proxy", so nothing special
                             beyond playing it — recorded so the grader knows a wake
                             must NOT have fired).
* ``wait-for-Proxy-done``  — block until Proxy stops speaking (via the injected
                             ``proxy_speaking`` signal), then play.
* ``keep-talking``         — play immediately even while Proxy is working (no wait).
* ``interrupt``            — play WHILE Proxy is speaking → barge-in. If the
                             ``proxy_speaking`` signal is available we wait until it
                             is True, then play mid-speech; otherwise play now and
                             flag that it needs live tuning.
* ``simultaneous``         — two beats spoken at once (played back-to-back with no
                             gap; the driver notes it for the grader).
* ``stay-silent``          — a Proxy/stage beat: never synthesized.

The ``proxy_speaking`` signal is a ``Callable[[], bool]`` the LIVE wiring supplies
from the meeting/relay audio state; when it is ``None`` the wait-gates fall back to
a bounded wait (``wait_budget_s``) — flagged as LIVE-TUNING-NEEDED in the SAID log.
Everything is injected so an offline test drives the whole gate matrix with fakes.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .replica import Replica
from .transcript import Beat, Chunk, Gate

#: A signal that is True while Proxy is currently speaking (from the meeting/relay
#: audio state). ``None`` ⇒ not cleanly available ⇒ the wait-gates use a bounded wait.
ProxySpeaking = Callable[[], bool]


@dataclass(frozen=True)
class SaidBeat:
    """One played line's log entry (SAID) — what a replica spoke, and how."""

    timestamp: str
    speaker: str
    gate: str
    line: str
    channel_id: str
    chunks_written: int
    played_at: float  # epoch when the line finished being pushed
    note: str = ""  # e.g. LIVE-TUNING-NEEDED for the timed gate fallbacks


@dataclass
class ChunkPlayback:
    """The result of playing one chunk — the SAID log + the window start marker."""

    checkpoint: str
    started_at: float
    finished_at: float
    said: list[SaidBeat] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint": self.checkpoint,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "said": [
                {
                    "timestamp": s.timestamp,
                    "speaker": s.speaker,
                    "gate": s.gate,
                    "line": s.line,
                    "channel_id": s.channel_id,
                    "chunks_written": s.chunks_written,
                    "played_at": s.played_at,
                    "note": s.note,
                }
                for s in self.said
            ],
        }


class Driver:
    """Drives replicas through the transcript, chunk by chunk (operator-paced)."""

    def __init__(
        self,
        chunks: list[Chunk],
        replicas: dict[str, Replica],
        *,
        meeting_url: str,
        run_dir: Path,
        proxy_speaking: ProxySpeaking | None = None,
        wait_budget_s: float = 8.0,
        poll_interval_s: float = 0.2,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._chunks = {c.checkpoint: c for c in chunks}
        self._replicas = replicas
        self._meeting_url = meeting_url
        self._run_dir = run_dir
        self._proxy_speaking = proxy_speaking
        self._wait_budget_s = wait_budget_s
        self._poll_interval_s = poll_interval_s
        self._now = clock
        self._sleep = sleep

    # -- lifecycle -----------------------------------------------------------

    async def setup(self, *, confirm_proxy: Callable[[], Awaitable[bool]] | None = None) -> dict[str, str]:
        """Join every replica to the meeting (idempotent) + confirm Proxy present.

        Returns ``{speaker: bot_id}``. ``confirm_proxy`` (live: a control-plane
        probe) is awaited when supplied; a False result raises so we never play a
        chunk into a room Proxy has not joined.
        """
        joined: dict[str, str] = {}
        for speaker, replica in self._replicas.items():
            joined[speaker] = await replica.join(self._meeting_url)
        if confirm_proxy is not None:
            present = await confirm_proxy()
            if not present:
                raise RuntimeError("Proxy is not present in the meeting — refusing to play")
        return joined

    async def teardown(self) -> None:
        """Remove every replica bot."""
        for replica in self._replicas.values():
            await replica.leave()

    # -- the chunk step ------------------------------------------------------

    def chunk(self, checkpoint: str) -> Chunk:
        if checkpoint not in self._chunks:
            raise KeyError(f"no chunk {checkpoint!r} (have {sorted(self._chunks)})")
        return self._chunks[checkpoint]

    async def play_chunk(self, checkpoint: str) -> ChunkPlayback:
        """Play chunk ``checkpoint``'s beats in order, honoring each beat's gate."""
        chunk = self.chunk(checkpoint)
        started = self._now()
        playback = ChunkPlayback(checkpoint=checkpoint, started_at=started, finished_at=started)
        for beat in chunk.beats:
            if not beat.playable:
                continue  # Proxy/stage beats are never synthesized
            said = await self._play_beat(beat)
            if said is not None:
                playback.said.append(said)
        playback.finished_at = self._now()
        self._write_said(playback)
        return playback

    async def replay_chunk(self, checkpoint: str) -> ChunkPlayback:
        """Re-play a chunk (the fix→replay loop) — same as ``play_chunk``.

        The SAID log is versioned by played timestamp so a replay never clobbers
        the prior attempt's record.
        """
        return await self.play_chunk(checkpoint)

    # -- gate mechanics ------------------------------------------------------

    async def _play_beat(self, beat: Beat) -> SaidBeat | None:
        replica = self._replicas.get(beat.speaker)
        if replica is None:
            # A speaker with no replica (e.g. Daksh, who "drives" but isn't a bot).
            # Recorded as a skipped line so the grader sees the gap, never silently dropped.
            return SaidBeat(
                timestamp=beat.timestamp, speaker=beat.speaker, gate=beat.gate.value,
                line=beat.line, channel_id="", chunks_written=0, played_at=self._now(),
                note="SKIPPED: no replica bot for this speaker",
            )

        note = await self._honor_gate(beat.gate)
        chunks = await replica.speak(beat.line)
        return SaidBeat(
            timestamp=beat.timestamp, speaker=beat.speaker, gate=beat.gate.value,
            line=beat.line, channel_id=replica.channel_id, chunks_written=chunks,
            played_at=self._now(), note=note,
        )

    async def _honor_gate(self, gate: Gate) -> str:
        """Apply the pre-speak timing for a gate; return a note for the SAID log."""
        if gate is Gate.WAIT_FOR_PROXY_DONE:
            return await self._wait_until_proxy_done()
        if gate is Gate.INTERRUPT:
            return await self._wait_until_proxy_speaking()
        # speak-now / don't-address / keep-talking / simultaneous: play immediately.
        return ""

    async def _wait_until_proxy_done(self) -> str:
        """Block until Proxy stops speaking (bounded). Note if the signal is absent."""
        if self._proxy_speaking is None:
            await self._bounded_sleep()
            return "LIVE-TUNING-NEEDED: no proxy_speaking signal; used bounded wait"
        deadline = self._now() + self._wait_budget_s
        while self._proxy_speaking() and self._now() < deadline:
            await self._sleep(self._poll_interval_s)
        return "" if self._now() < deadline else "wait budget elapsed while Proxy still speaking"

    async def _wait_until_proxy_speaking(self) -> str:
        """Barge-in: wait until Proxy IS speaking, then return so the line plays over it."""
        if self._proxy_speaking is None:
            return "LIVE-TUNING-NEEDED: no proxy_speaking signal; interrupt played immediately"
        deadline = self._now() + self._wait_budget_s
        while not self._proxy_speaking() and self._now() < deadline:
            await self._sleep(self._poll_interval_s)
        return "" if self._proxy_speaking() else "Proxy never spoke within budget; interrupt may miss"

    async def _bounded_sleep(self) -> None:
        await self._sleep(self._wait_budget_s)

    # -- SAID persistence ----------------------------------------------------

    def _write_said(self, playback: ChunkPlayback) -> None:
        """Append the SAID log for this play to ``live-runs/<run>/<CP>/said-*.json``."""
        chunk_dir = self._run_dir / playback.checkpoint
        chunk_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(playback.started_at * 1000)
        path = chunk_dir / f"said-{stamp}.json"
        path.write_text(json.dumps(playback.to_dict(), indent=2), encoding="utf-8")
