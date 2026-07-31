"""The no-model notes store — Proxy's running memory of the meeting (Task M1).

The transcript IS the notes (SPEC §2: "store it raw as the notes. No model on
the transcript."). The transport stream already delivers cleaned lines
(fillers removed, punctuated, speaker-labelled); this module only accumulates
them in arrival order and renders them as agent-readable text. No model call
exists anywhere on this path — the module imports nothing but the stdlib.

In-memory per meeting for now: one ``NotesStore`` instance is one meeting's
running transcript. The store is the single seam the orchestrator loop reads
for context and the safeguards read to catch up on reconnect, so a durable
(Postgres) backing can later slot in behind the same methods without changing
any caller.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    """One cleaned transcript segment, exactly as the transport stream delivers it."""

    text: str
    speaker: str
    timestamp: float
    end_of_turn: bool


class NotesStore:
    """The running transcript of ONE meeting, accumulated in arrival order."""

    def __init__(self) -> None:
        self._lines: list[TranscriptLine] = []

    def append(self, line: TranscriptLine) -> None:
        """Append one cleaned line to the running transcript (arrival order)."""
        self._lines.append(line)

    def lines(self) -> tuple[TranscriptLine, ...]:
        """The raw accumulated lines, oldest first, as an immutable snapshot."""
        return tuple(self._lines)

    def transcript(self) -> str:
        """The full running transcript as speaker-attributed, agent-readable text."""
        return _render(self._lines)

    def recent(self, count: int) -> str:
        """The last ``count`` lines (the tail) as speaker-attributed text.

        A ``count`` wider than the store returns the full transcript; a
        non-positive ``count`` is a caller bug and fails loud.
        """
        if count <= 0:
            raise ValueError(f"recent window must be at least 1 line, got {count}")
        return _render(self._lines[-count:])

    def __len__(self) -> int:
        return len(self._lines)


def _render(lines: Iterable[TranscriptLine]) -> str:
    """Render lines as one speaker-attributed text block, one line per segment."""
    return "\n".join(f"{line.speaker}: {line.text}" for line in lines)
