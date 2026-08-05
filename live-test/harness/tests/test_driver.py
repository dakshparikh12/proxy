"""Offline tests for the driver + replicas — the gate matrix, no network.

A ``FakeTransport`` / ``FakeTTS`` / ``FakeChannel`` stand in for Recall / Cartesia
/ Output-Media so ``play-chunk`` pushes the RIGHT audio to the RIGHT replica per
gate, entirely in-process.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from harness.driver import Driver
from harness.replica import Replica
from harness.transcript import Acceptance, Beat, Chunk, Gate


@dataclass
class _Chunk:
    """One fake AudioChunk (the transport.media.AudioChunk shape: has ``.pcm``)."""

    pcm: bytes


class FakeTTS:
    def __init__(self, chunks_per_line: int = 3) -> None:
        self._n = chunks_per_line
        self.synthesized: list[str] = []

    async def synthesize(self, text: str):  # noqa: ANN201
        self.synthesized.append(text)
        for i in range(self._n):
            yield _Chunk(pcm=f"{text}:{i}".encode())


@dataclass
class FakeChannel:
    channel_id: str
    audio: list[bytes] = field(default_factory=list)
    speaking_flips: list[bool] = field(default_factory=list)

    async def write_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def set_speaking(self, speaking: bool) -> None:
        self.speaking_flips.append(speaking)


class FakeTransport:
    def __init__(self, bot_id: str) -> None:
        self._bot_id = bot_id
        self.joins: list[str] = []
        self.left: list[str] = []

    async def join(self, meeting_link: str) -> str:
        self.joins.append(meeting_link)
        return self._bot_id

    async def leave(self, bot_id: str) -> None:
        self.left.append(bot_id)


def _replica(name: str) -> tuple[Replica, FakeChannel, FakeTTS, FakeTransport]:
    channel = FakeChannel(channel_id=name.lower())
    tts = FakeTTS()
    transport = FakeTransport(bot_id=f"bot-{name.lower()}")
    return (
        Replica(
            speaker=name, channel_id=name.lower(),
            transport=transport, tts=tts, channel=channel,
        ),
        channel, tts, transport,
    )


def _beat(speaker: str, gate: Gate, line: str, ts: str = "T+00:00") -> Beat:
    return Beat(
        timestamp=ts, speaker=speaker, gate=gate, line=line,
        is_proxy=False, is_stage=False, acceptance=Acceptance(),
    )


def _proxy_beat() -> Beat:
    return Beat(
        timestamp="T+00:10", speaker="Proxy", gate=Gate.STAY_SILENT,
        line="", is_proxy=True, is_stage=True, acceptance=Acceptance(),
    )


@pytest.mark.asyncio
async def test_replica_speak_pushes_audio_to_its_own_channel() -> None:
    replica, channel, tts, _ = _replica("Riya")
    n = await replica.speak("hello there")
    assert n == 3
    assert channel.audio == [b"hello there:0", b"hello there:1", b"hello there:2"]
    assert channel.speaking_flips == [True, False]  # bracketed
    assert tts.synthesized == ["hello there"]
    assert replica.said == ["hello there"]


@pytest.mark.asyncio
async def test_setup_joins_all_replicas_idempotently() -> None:
    riya, _, _, r_tp = _replica("Riya")
    pranav, _, _, p_tp = _replica("Pranav")
    driver = Driver(
        [], {"Riya": riya, "Pranav": pranav},
        meeting_url="https://meet/x", run_dir=_tmp(),
    )
    joined = await driver.setup()
    assert joined == {"Riya": "bot-riya", "Pranav": "bot-pranav"}
    # idempotent: a second setup does not re-join.
    await driver.setup()
    assert r_tp.joins == ["https://meet/x"]
    assert p_tp.joins == ["https://meet/x"]


@pytest.mark.asyncio
async def test_setup_refuses_when_proxy_absent() -> None:
    riya, _, _, _ = _replica("Riya")
    driver = Driver([], {"Riya": riya}, meeting_url="m", run_dir=_tmp())

    async def absent() -> bool:
        return False

    with pytest.raises(RuntimeError, match="not present"):
        await driver.setup(confirm_proxy=absent)


@pytest.mark.asyncio
async def test_play_chunk_routes_each_line_to_the_named_replica(tmp_path) -> None:
    riya, r_ch, _, _ = _replica("Riya")
    pranav, p_ch, _, _ = _replica("Pranav")
    chunk = Chunk(
        checkpoint="CP-1", part="A", title="t",
        beats=(
            _beat("Riya", Gate.SPEAK_NOW, "riya one"),
            _beat("Pranav", Gate.DONT_ADDRESS, "pranav two"),
            _proxy_beat(),  # never synthesized
        ),
    )
    driver = Driver([chunk], {"Riya": riya, "Pranav": pranav},
                    meeting_url="m", run_dir=tmp_path)
    playback = await driver.play_chunk("CP-1")

    # Proxy/stage beat filtered; each line went to its OWN channel.
    assert [s.speaker for s in playback.said] == ["Riya", "Pranav"]
    assert r_ch.audio and all(b.startswith(b"riya one") for b in r_ch.audio)
    assert p_ch.audio and all(b.startswith(b"pranav two") for b in p_ch.audio)
    # SAID log persisted.
    saids = list((tmp_path / "CP-1").glob("said-*.json"))
    assert len(saids) == 1


@pytest.mark.asyncio
async def test_wait_for_proxy_done_gate_blocks_until_signal_clears(tmp_path) -> None:
    riya, _, _, _ = _replica("Riya")
    speaking = {"v": True}
    ticks = {"n": 0}

    def proxy_speaking() -> bool:
        # Clears after two polls — the gate must wait for it.
        ticks["n"] += 1
        if ticks["n"] >= 3:
            speaking["v"] = False
        return speaking["v"]

    async def fast_sleep(_s: float) -> None:
        return None

    chunk = Chunk(
        checkpoint="CP-1", part="A", title="t",
        beats=(_beat("Riya", Gate.WAIT_FOR_PROXY_DONE, "after proxy"),),
    )
    driver = Driver([chunk], {"Riya": riya}, meeting_url="m", run_dir=tmp_path,
                    proxy_speaking=proxy_speaking, sleep=fast_sleep)
    playback = await driver.play_chunk("CP-1")
    assert playback.said[0].note == ""  # cleared within budget, no warning
    assert ticks["n"] >= 3


@pytest.mark.asyncio
async def test_wait_gate_without_signal_flags_live_tuning(tmp_path) -> None:
    riya, _, _, _ = _replica("Riya")

    async def fast_sleep(_s: float) -> None:
        return None

    chunk = Chunk(
        checkpoint="CP-1", part="A", title="t",
        beats=(_beat("Riya", Gate.WAIT_FOR_PROXY_DONE, "x"),),
    )
    driver = Driver([chunk], {"Riya": riya}, meeting_url="m", run_dir=tmp_path,
                    proxy_speaking=None, sleep=fast_sleep)
    playback = await driver.play_chunk("CP-1")
    assert "LIVE-TUNING-NEEDED" in playback.said[0].note


@pytest.mark.asyncio
async def test_interrupt_gate_waits_until_proxy_is_speaking(tmp_path) -> None:
    riya, _, _, _ = _replica("Riya")
    ticks = {"n": 0}

    def proxy_speaking() -> bool:
        ticks["n"] += 1
        return ticks["n"] >= 2  # starts False, becomes True → interrupt fires

    async def fast_sleep(_s: float) -> None:
        return None

    chunk = Chunk(
        checkpoint="CP-1", part="A", title="t",
        beats=(_beat("Riya", Gate.INTERRUPT, "cut in"),),
    )
    driver = Driver([chunk], {"Riya": riya}, meeting_url="m", run_dir=tmp_path,
                    proxy_speaking=proxy_speaking, sleep=fast_sleep)
    playback = await driver.play_chunk("CP-1")
    assert playback.said[0].note == ""  # Proxy was speaking → interrupt landed


@pytest.mark.asyncio
async def test_unknown_speaker_recorded_not_dropped(tmp_path) -> None:
    riya, _, _, _ = _replica("Riya")
    chunk = Chunk(
        checkpoint="CP-1", part="A", title="t",
        beats=(_beat("Daksh", Gate.SPEAK_NOW, "daksh drives"),),
    )
    driver = Driver([chunk], {"Riya": riya}, meeting_url="m", run_dir=tmp_path)
    playback = await driver.play_chunk("CP-1")
    assert playback.said[0].speaker == "Daksh"
    assert "SKIPPED" in playback.said[0].note


@pytest.mark.asyncio
async def test_teardown_leaves_every_bot(tmp_path) -> None:
    riya, _, _, r_tp = _replica("Riya")
    driver = Driver([], {"Riya": riya}, meeting_url="m", run_dir=tmp_path)
    await driver.setup()
    await driver.teardown()
    assert r_tp.left == ["bot-riya"]


def _tmp():  # noqa: ANN202
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp())
