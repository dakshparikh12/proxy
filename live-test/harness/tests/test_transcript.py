"""Offline unit tests for the transcript parser — deterministic, no network."""
from __future__ import annotations

from conftest import TRANSCRIPT_PATH

from harness.transcript import Chunk, Gate, load_transcript, parse_transcript

# A tiny fixture covering every grammar element the driver depends on.
_SAMPLE = """
# preamble ignored
> facts ignored

## PART A — Everyone piles in (G1: join, hear, transcribe, speak)

**[T+00:00]** Daksh (system): *[opens the meeting; Proxy is invited]*
- **SCN:** G1-01 (Proxy joins).
- **PROCESS:** join once, post consent first.
- **ROUTING:** consent line -> meeting chat (once).
- **OUTPUT (sane):** one consent line visible.

**[T+00:12]** Riya *(speak-now)*: "Can you guys hear me? Riya here."
- **SCN:** G1-02 (hello).
- **PROCESS:** `stay-silent` (not addressed).
- **ROUTING:** none.

**[T+00:20]** Pranav *(don't-address)*: "Just chatting, no wake word."
- **SCN:** G1-03.
- **PROCESS:** `stay-silent`.

## PART B — Getting oriented (G2: resident codebase understanding)

**[T+03:10]** Daksh *(speak-now)*: "Proxy, where's the entrypoint?"
- **SCN:** G2-01 (entrypoint).
- **PROCESS (declared, exact flow):** `zero-read-cache` answer.
- **ROUTING (declared):** voice-gist.
- **OUTPUT (sane):** grounded file:line.

**[T+24:15]** Proxy -> screen + *(voice)*: "Here's the analysis."
- **SCN:** G5-10 (present-back).
- **PROCESS:** present-back.
"""


def test_parses_two_parts_into_checkpoint_keyed_chunks() -> None:
    chunks = parse_transcript(_SAMPLE)
    assert [c.checkpoint for c in chunks] == ["CP-1", "CP-2"]
    assert [c.part for c in chunks] == ["A", "B"]
    assert chunks[0].title.startswith("Everyone piles in")


def test_stage_direction_beat_is_not_playable() -> None:
    a = parse_transcript(_SAMPLE)[0]
    stage = a.beats[0]
    assert stage.is_stage is True
    assert stage.playable is False
    assert stage.speaker == "Daksh"
    assert stage.acceptance.scn.startswith("G1-01")
    assert stage.scenario_ids == ("G1-01",)


def test_spoken_line_parsed_with_gate_and_text() -> None:
    a = parse_transcript(_SAMPLE)[0]
    riya = a.beats[1]
    assert riya.speaker == "Riya"
    assert riya.gate is Gate.SPEAK_NOW
    assert riya.line == "Can you guys hear me? Riya here."
    assert riya.playable is True


def test_dont_address_gate_lifted() -> None:
    a = parse_transcript(_SAMPLE)[0]
    pranav = a.beats[2]
    assert pranav.gate is Gate.DONT_ADDRESS
    assert pranav.playable is True  # still spoken — just without a wake word


def test_proxy_beat_is_not_playable() -> None:
    b = parse_transcript(_SAMPLE)[1]
    proxy = b.beats[-1]
    assert proxy.is_proxy is True
    assert proxy.playable is False


def test_full_acceptance_captured() -> None:
    b = parse_transcript(_SAMPLE)[1]
    ask = b.beats[0]
    assert ask.acceptance.process.startswith("`zero-read-cache`")
    assert ask.acceptance.routing == "voice-gist."
    assert "grounded" in ask.acceptance.output


def test_playable_beats_filters_proxy_and_stage() -> None:
    a = parse_transcript(_SAMPLE)[0]
    # stage(Daksh) excluded; Riya + Pranav included.
    assert [b.speaker for b in a.playable_beats] == ["Riya", "Pranav"]


# ── the real file: the grammar must hold end-to-end ─────────────────────────


def test_real_transcript_parses_all_eleven_checkpoints() -> None:
    chunks = load_transcript(TRANSCRIPT_PATH)
    assert [c.checkpoint for c in chunks] == [f"CP-{i}" for i in range(1, 12)]
    assert [c.part for c in chunks] == list("ABCDEFGHIJK")


def test_real_transcript_has_beats_in_every_chunk() -> None:
    for chunk in load_transcript(TRANSCRIPT_PATH):
        assert chunk.beats, f"{chunk.checkpoint} parsed with zero beats"
        assert chunk.playable_beats, f"{chunk.checkpoint} has no playable lines"


def test_real_transcript_gate_coverage() -> None:
    gates = {b.gate for c in load_transcript(TRANSCRIPT_PATH) for b in c.beats}
    # The three the driver builds solidly must all appear.
    assert Gate.SPEAK_NOW in gates
    assert Gate.DONT_ADDRESS in gates
    assert Gate.KEEP_TALKING in gates


def test_beats_ordered_by_appearance() -> None:
    for chunk in load_transcript(TRANSCRIPT_PATH):
        stamps = [b.timestamp for b in chunk.beats]
        assert stamps == sorted(stamps, key=_ts_key), chunk.checkpoint


def _ts_key(ts: str) -> tuple[int, int]:
    body = ts.removeprefix("T+")
    mm, ss = body.split(":")
    return int(mm), int(ss)


def test_chunk_is_frozen_dataclass() -> None:
    chunk = Chunk(checkpoint="CP-1", part="A", title="x")
    assert chunk.beats == ()
