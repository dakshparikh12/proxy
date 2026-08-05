"""Parse ``MEETING_TRANSCRIPT.md`` into ordered, checkpoint-delimited chunks.

Deterministic and offline: no I/O beyond reading the one markdown file, no
network, fully unit-testable. The grammar we parse (observed in the real file):

* Parts are ``## PART <LETTER> — ...`` headings; each Part is one chunk, keyed by
  its checkpoint ``CP-<n>`` (Part A → CP-1, B → CP-2, ...).
* A beat starts with ``**[T+mm:ss]** <SPEAKER>`` optionally followed by an italic
  ``*(gate, notes)*`` and then either ``: "<line>"`` (a spoken line) or ``: *[...]*``
  (a stage direction / system beat). ``Proxy → ...`` beats are Proxy's OWN turns.
* Sub-bullets under a beat carry the declared acceptance:
  ``- **SCN:** ...``  ``- **PROCESS:** ...``  ``- **ROUTING:** ...``
  ``- **OUTPUT (sane):** ...``.

The gate (``speak-now`` / ``don't-address`` / ``wait-for-Proxy-done`` /
``keep-talking`` / ``interrupt`` / ``simultaneous`` / ``stay-silent``) is lifted
from the italic ``*(...)*`` note; when absent it defaults to ``speak-now`` for a
spoken line and ``stay-silent`` for a stage direction (which the driver never
synthesizes).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Gate(str, Enum):
    """The bot-gate a beat is played under — how the replica enacts the line."""

    SPEAK_NOW = "speak-now"
    DONT_ADDRESS = "don't-address"
    WAIT_FOR_PROXY_DONE = "wait-for-Proxy-done"
    KEEP_TALKING = "keep-talking"
    INTERRUPT = "interrupt"
    SIMULTANEOUS = "simultaneous"
    STAY_SILENT = "stay-silent"


# The canonical gate tokens, longest-first so ``keep-talking`` never matches
# inside another token and the exact spellings (incl. the apostrophe) are honored.
_GATE_TOKENS: tuple[Gate, ...] = (
    Gate.WAIT_FOR_PROXY_DONE,
    Gate.DONT_ADDRESS,
    Gate.KEEP_TALKING,
    Gate.SIMULTANEOUS,
    Gate.STAY_SILENT,
    Gate.INTERRUPT,
    Gate.SPEAK_NOW,
)


@dataclass(frozen=True)
class Acceptance:
    """The four-part acceptance declared for a beat (graded against the trace)."""

    scn: str = ""
    process: str = ""
    routing: str = ""
    output: str = ""


@dataclass(frozen=True)
class Beat:
    """One transcript beat: a line to play (or a Proxy/stage beat we don't play)."""

    timestamp: str
    speaker: str
    gate: Gate
    line: str
    is_proxy: bool
    is_stage: bool
    acceptance: Acceptance
    scenario_ids: tuple[str, ...] = ()

    @property
    def playable(self) -> bool:
        """A non-Proxy, non-stage spoken line the driver actually synthesizes."""
        return bool(self.line) and not self.is_proxy and not self.is_stage


@dataclass(frozen=True)
class Chunk:
    """One Part of the meeting (a pausable checkpoint), with its ordered beats."""

    checkpoint: str  # e.g. "CP-1"
    part: str  # e.g. "A"
    title: str
    beats: tuple[Beat, ...] = field(default_factory=tuple)

    @property
    def playable_beats(self) -> tuple[Beat, ...]:
        return tuple(b for b in self.beats if b.playable)


# ── line grammar ────────────────────────────────────────────────────────────

_PART_RE = re.compile(r"^##\s+PART\s+([A-Z])\s*(?:—|-)\s*(.*)$")
# **[T+mm:ss]** <lead>: rest
#   The content boundary is the ``: `` (or ``:`` at EOL) that INTRODUCES the spoken
#   ``"..."`` / stage ``*[...]*``. A colon inside the quoted line never reaches here
#   because ``rest`` greedily takes everything after the FIRST content-introducing
#   colon. ``<lead>`` = speaker + any note; ``_speaker_of`` / ``_note_of`` split it.
_BEAT_RE = re.compile(r"^\*\*\[(?P<ts>T\+[0-9:]+)\]\*\*\s+(?P<lead>.*?):\s*(?P<rest>.*)$")
# The speaker is the leading run before the first ``*``, ``(``, ``→``, or ``+``.
_SPEAKER_RE = re.compile(r"^\s*(?P<speaker>[^*(→+:]+)")
# A note is a parenthesized group in the lead, italic ``*( )*`` or plain ``( )``.
_NOTE_RE = re.compile(r"\*?\((?P<note>[^)]*)\)\*?")
_SUB_RE = re.compile(r"^-\s+\*\*(?P<key>[A-Za-z ,/()—-]+?):\*\*\s*(?P<val>.*)$")
_SCN_ID_RE = re.compile(r"\b(G\d+-\d+|CP-\d+)\b")
_SPOKEN_RE = re.compile(r'^"(?P<line>.*)"\s*$')


def _part_to_checkpoint(part: str) -> str:
    """Part A → CP-1, B → CP-2 ... (the file's own mapping, §CHECKPOINTS)."""
    return f"CP-{ord(part) - ord('A') + 1}"


def _speaker_of(lead: str) -> str:
    """The speaker name from a beat's lead (before any note / ``→`` / ``*``)."""
    match = _SPEAKER_RE.match(lead)
    return match.group("speaker").strip() if match is not None else lead.strip()


def _note_of(lead: str) -> str:
    """The parenthesized note from a beat's lead ("" when there is none)."""
    match = _NOTE_RE.search(lead)
    return match.group("note").strip() if match is not None else ""


def _classify_gate(note: str, *, is_stage: bool) -> Gate:
    """Lift the gate from the italic ``*(...)*`` note; default by beat kind."""
    lowered = note.lower()
    for token in _GATE_TOKENS:
        if token.value in lowered:
            return token
    return Gate.STAY_SILENT if is_stage else Gate.SPEAK_NOW


def _extract_line(rest: str) -> tuple[str, bool]:
    """From a beat's trailing text return (spoken_line, is_stage_direction)."""
    rest = rest.strip()
    match = _SPOKEN_RE.match(rest)
    if match is not None:
        return match.group("line").strip(), False
    # A stage direction (``*[...]*``) or any non-quoted trailer is not spoken.
    return "", True


def _acceptance_from(sub: dict[str, str]) -> Acceptance:
    """Assemble the 4-part acceptance from collected sub-bullet lines."""

    def pick(*keys: str) -> str:
        for key in keys:
            if key in sub:
                return sub[key]
        return ""

    return Acceptance(
        scn=pick("SCN"),
        # The file uses "PROCESS", "PROCESS (declared)", "PROCESS (declared, exact flow)".
        process=pick("PROCESS", "PROCESS (declared)", "PROCESS (declared, exact flow)"),
        routing=pick("ROUTING", "ROUTING (declared)"),
        output=pick("OUTPUT (sane)", "OUTPUT", "OUTPUT (should be good — NOT graded)"),
    )


def _finish_beat(
    header: dict[str, str], sub: dict[str, str], beats: list[Beat]
) -> None:
    """Materialize the currently-open beat (if any) and append it."""
    if not header:
        return
    line = header["line"]
    is_stage = header["is_stage"] == "1"
    acceptance = _acceptance_from(sub)
    scenario_ids = tuple(_SCN_ID_RE.findall(acceptance.scn))
    beats.append(
        Beat(
            timestamp=header["ts"],
            speaker=header["speaker"],
            gate=Gate(header["gate"]),
            line=line,
            is_proxy=header["is_proxy"] == "1",
            is_stage=is_stage,
            acceptance=acceptance,
            scenario_ids=scenario_ids,
        )
    )


def parse_transcript(text: str) -> list[Chunk]:
    """Parse the transcript markdown into an ordered list of :class:`Chunk`.

    Everything before the first ``## PART`` heading (the preamble, the facts
    table, the checkpoint legend) is ignored — only Part sections become chunks.
    """
    chunks: list[Chunk] = []
    cur_beats: list[Beat] = []
    cur_part: str | None = None
    cur_title = ""
    open_header: dict[str, str] = {}
    open_sub: dict[str, str] = {}

    def close_beat() -> None:
        nonlocal open_header, open_sub
        _finish_beat(open_header, open_sub, cur_beats)
        open_header = {}
        open_sub = {}

    def close_chunk() -> None:
        nonlocal cur_beats
        close_beat()
        if cur_part is not None:
            chunks.append(
                Chunk(
                    checkpoint=_part_to_checkpoint(cur_part),
                    part=cur_part,
                    title=cur_title,
                    beats=tuple(cur_beats),
                )
            )
        cur_beats = []

    for raw in text.splitlines():
        line = raw.rstrip()

        part_m = _PART_RE.match(line)
        if part_m is not None:
            close_chunk()
            cur_part = part_m.group(1)
            cur_title = part_m.group(2).strip()
            continue

        if cur_part is None:
            continue  # still in the preamble

        beat_m = _BEAT_RE.match(line)
        if beat_m is not None:
            close_beat()
            lead = beat_m.group("lead")
            speaker = _speaker_of(lead)
            note = _note_of(lead)
            spoken, is_stage = _extract_line(beat_m.group("rest"))
            is_proxy = speaker.lower().startswith("proxy")
            open_header = {
                "ts": beat_m.group("ts"),
                "speaker": speaker,
                "gate": _classify_gate(note, is_stage=is_stage).value,
                "line": spoken,
                "is_proxy": "1" if is_proxy else "0",
                "is_stage": "1" if is_stage else "0",
            }
            continue

        sub_m = _SUB_RE.match(line)
        if sub_m is not None and open_header:
            open_sub[sub_m.group("key").strip()] = sub_m.group("val").strip()
            continue

    close_chunk()
    return chunks


def load_transcript(path: str | Path) -> list[Chunk]:
    """Read and parse the transcript file at ``path``."""
    return parse_transcript(Path(path).read_text(encoding="utf-8"))
