"""Real-time meeting player — deliver each line at its timestamp through the REAL intake.

This is the "manual meeting testing, automated, minus the audio" core. It does NOT
batch-dump lines and drain between them (that's the deterministic battery's scoring
mode). Instead it delivers each transcript line at its (compressed) timestamp through
the SAME code path the real product runs on a webhook — the barge-in cut reflex FIRST,
then ``engine.feed_transcript`` — WITHOUT draining between lines, so barge-in,
concurrency, and moved-on happen authentically:

* the real ``control_plane.webhooks._cut_speech_on_human_voice(runtime, body,
  meeting_id)`` runs on every line — human speech landing while Proxy's real
  ``SpeakPipe`` is mid-utterance CUTS it (Law 3), exactly as in production;
* ``engine.feed_transcript(line)`` spawns the wake turn as a BACKGROUND task, so the
  next line is delivered while Proxy is still working — a line scheduled to land during
  a turn really does (barge-in), and a slow task really is still running while the room
  keeps talking (moved-on).

Time is PROPORTIONAL-COMPRESSED by default: absolute inter-line gaps are scaled down
(``compression``) and capped (``max_wait_s``) so a ~20-minute meeting plays in a few
minutes, but RELATIVE ordering is preserved — a barge line still lands mid-turn, a
moved-on gap still elapses after a slow ask. ``compression`` and ``max_wait_s`` are
params.

The Engine is built with its FULL real access, DB-free: the real ``RepoContext`` code
server over the primed clone, the real ``meeting_control`` server over a recording
``FakeMeetingTransport``, the real ``sandbox`` server over a provisioned E2B handle
(when ``live_e2b``), and the real ``drafts`` server with a RECORDING stage seam (the
tool path runs for real; DB persistence is faked). The provider is the real
``EngineProvider`` on the subscription (paid key popped), teed by ``TracingProvider``.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.eval.meeting_monitor import MeetingMonitor
from tests.eval.plan_trace import TracingProvider

__all__ = ["PlayerResult", "StagedDraft", "play_meeting"]

BOT_ID = "proxy-e2e-bot"
MEETING_ID = "e2e-smoke-meeting"
MODEL = "claude-sonnet-4-6"
MAX_TURNS = 24


# ── The recording seams (real handler path; persistence faked) ────────────────


class FakeMeetingTransport:
    """Records every meeting-control verb the REAL MCP handlers drive."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def mute(self, bot_id: str) -> None:
        self.calls.append(("mute", {"bot_id": bot_id}))

    async def unmute(self, bot_id: str) -> None:
        self.calls.append(("unmute", {"bot_id": bot_id}))

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.calls.append(("post_chat", {"bot_id": bot_id, "message": message, "pinned": pinned}))

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.calls.append(
            ("send_dm", {"bot_id": bot_id, "message": message, "participant_id": participant_id})
        )


@dataclass(slots=True)
class StagedDraft:
    """One draft the real propose_change handler tried to stage (persistence faked)."""

    draft_id: str
    meeting_id: str
    kind: str
    summary: str
    files: Any
    unified_diff: Any


class _RecordingStage:
    """The injectable ``StageFn`` for ``build_drafts_server`` — records + returns a
    ``draft_id``-bearing result, so the propose_change tool path runs FOR REAL
    (schema validation, never-throw boundary, approve_url) while DB persistence is
    faked (Law 3 gate is genuinely exercised — nothing world-touching lands)."""

    def __init__(self) -> None:
        self.staged: list[StagedDraft] = []
        self._n = 0

    async def __call__(self, _db: Any, *, meeting_id: Any, kind: str, summary: str,
                       files: Any = None, unified_diff: Any = None) -> Any:
        self._n += 1
        draft_id = f"e2e-draft-{self._n:03d}"
        self.staged.append(StagedDraft(
            draft_id=draft_id, meeting_id=str(meeting_id), kind=kind, summary=summary,
            files=files, unified_diff=unified_diff,
        ))

        @dataclass
        class _Result:
            draft_id: str
        return _Result(draft_id=draft_id)


# ── The fake runtime (the exact attrs the webhook barge-in + feed read) ───────


@dataclass(slots=True)
class _FakeRuntime:
    """The minimal runtime shim the REAL webhook path reads: ``.speak_pipe`` +
    ``.engine`` (``webhooks.py:233,355``). Nothing else is touched on the transcript
    branch we drive, so this is the faithful in-process stand-in."""

    speak_pipe: Any
    engine: Any


# ── The speak channel recorder (fake audio out; real SpeakPipe) ───────────────


class _RecordingChannel:
    """A fake ``AudioOut`` — records set_speaking / audio writes so barge-in cuts are
    OBSERVABLE. The real ``SpeakPipe`` drives it, so its ``.speaking`` guard + ``.cut()``
    are the genuine product objects the barge-in reflex acts on."""

    def __init__(self) -> None:
        self.speaking_events: list[tuple[float, bool]] = []
        self.audio_bytes = 0
        self._t0 = time.perf_counter()

    async def write_audio(self, pcm: bytes) -> None:
        self.audio_bytes += len(pcm)

    async def set_speaking(self, speaking: bool) -> None:
        self.speaking_events.append((time.perf_counter() - self._t0, speaking))


async def _fake_synthesize(text: str):
    """A fake synth: yields a little pcm per sentence, with a small delay so the pipe
    is genuinely 'speaking' for a beat (long enough for a barge line to catch it)."""
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _Chunk:
        pcm: bytes

    # A few chunks with tiny awaits — keeps the pipe mid-utterance briefly & realistically.
    for _ in range(3):
        await asyncio.sleep(0.05)
        yield _Chunk(pcm=b"\x00\x01" * 200)


# ── The result ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PlayerResult:
    """Everything the run produced, for the monitor + judge (all ground truth)."""

    meeting_id: str
    traced: TracingProvider
    monitor: MeetingMonitor
    transport: FakeMeetingTransport
    stage: _RecordingStage
    channel: _RecordingChannel
    engine: Any
    barge_cuts: int
    sandbox_mounted: bool
    #: ask_id -> t_wake (perf_counter at the moment the ask line was fed)
    t_wakes: dict[str, float] = field(default_factory=dict)


# ── The player ────────────────────────────────────────────────────────────────


def _disambiguate_factory() -> Any:
    """The trigger judgment seam: an addressed 'proxy ...' line wakes; a common-noun
    'proxy' does not. We reuse the engine's OWN word-hit gate (the trigger only calls
    disambiguate on a ``\\bproxy\\b`` hit) and pin it deterministic-but-faithful: wake
    when the line is directly addressing Proxy (starts with 'proxy' or 'hey proxy' /
    'proxy,'), decline otherwise — matching how a human addresses the agent."""
    import re
    addressed = re.compile(r"^\s*(hey\s+|ok\s+|okay\s+)?proxy[\s,:]", re.IGNORECASE)

    async def disambiguate(text: str) -> bool:
        return addressed.match(text) is not None

    return disambiguate


async def play_meeting(
    meeting: Any,
    *,
    clone_path: Path,
    map_text: str | None,
    compression: float = 0.04,
    max_wait_s: float = 0.5,
    live_e2b: bool = False,
    model: str = MODEL,
    drain_timeout_s: float = 900.0,
) -> PlayerResult:
    """Play ``meeting`` in real time through the REAL engine + intake path.

    ``compression`` scales absolute inter-line gaps (0.04 → a 25s gap plays in 1s);
    ``max_wait_s`` caps any single wait so the run stays bounded while relative
    ordering (mid-turn barge / after-slow moved-on) is preserved. ``live_e2b=True``
    provisions a REAL E2B sandbox (killed at the end). ``drain_timeout_s`` hard-caps
    the final drain so a single hung SDK stream can never stall the whole run. Returns
    a ``PlayerResult`` the monitor + judge consume.
    """
    os.environ.pop("ANTHROPIC_API_KEY", None)  # subscription CLI auth only

    from in_meeting.drafts_access import DRAFT_TOOLS, build_drafts_server
    from in_meeting.engine import CODE_TOOLS, Engine
    from in_meeting.meeting_control import MEETING_TOOLS, build_meeting_control_server
    from in_meeting.notes import TranscriptLine
    from in_meeting.provider import EngineProvider
    from in_meeting.speak import build_speak_sink
    from premeeting.repo_context import RepoContext

    monitor = MeetingMonitor()
    monitor.mark("meeting-start", f"{meeting.id} ({meeting.meeting_type})")

    # The grounded code server over the REAL primed clone.
    code_server = RepoContext(
        clone_path=clone_path, map_text=map_text, tenant_id="e2e-smoke"
    ).build_server()
    if code_server is None:
        raise RuntimeError(f"code server did not build over clone {clone_path}")

    transport = FakeMeetingTransport()
    stage = _RecordingStage()
    drafts_server = build_drafts_server(db=object(), meeting_id=MEETING_ID, stage=stage)
    if drafts_server is None:
        raise RuntimeError("drafts server did not build with the recording stage seam")

    mcp_servers: dict[str, Any] = {
        "code_intel": code_server,
        "meeting": build_meeting_control_server(transport, bot_id=BOT_ID),
        "drafts": drafts_server,
    }
    allowed_tools: tuple[str, ...] = tuple(CODE_TOOLS) + tuple(MEETING_TOOLS) + tuple(DRAFT_TOOLS)

    sandbox_handle: Any | None = None
    if live_e2b:
        from in_meeting.sandbox import SANDBOX_TOOLS, build_sandbox_server, provision_sandbox

        monitor.mark("sandbox", "provisioning E2B")
        sandbox_handle = await provision_sandbox()
        mcp_servers["sandbox"] = build_sandbox_server(sandbox_handle)
        allowed_tools = allowed_tools + tuple(SANDBOX_TOOLS)
        monitor.mark("sandbox", "E2B provisioned")

    # The real speak pipe (the barge-in target) over recording fakes.
    channel = _RecordingChannel()
    speak_pipe = build_speak_sink(synthesize=_fake_synthesize, channel=channel)

    provider = EngineProvider()
    traced = TracingProvider(provider)

    engine = Engine(
        model=model,
        allowed_tools=allowed_tools,
        speak=speak_pipe,           # the SpeakPipe IS the SpeakSink (has .say/.commit_tail)
        disambiguate=_disambiguate_factory(),
        provider=traced,
        map_text=map_text,
        mcp_servers=mcp_servers,
        max_turns=MAX_TURNS,
    )
    monitor.mark("consent", "engine live (no consent gate on feed; notes plane N/A in-process)")

    runtime = _FakeRuntime(speak_pipe=speak_pipe, engine=engine)

    # The real webhook seams (barge-in cut runs FIRST, exactly as production).
    from control_plane.webhooks import _cut_speech_on_human_voice

    result = PlayerResult(
        meeting_id=meeting.id, traced=traced, monitor=monitor, transport=transport,
        stage=stage, channel=channel, engine=engine, barge_cuts=0,
        sandbox_mounted=live_e2b,
    )

    ask_by_key: dict[tuple[float, str], Any] = {
        (a.ts, " ".join(a.ask.lower().split())): a for a in meeting.asks
    }
    follow_ups: dict[float, Any] = {
        a.follow_up_ts: a for a in meeting.asks
        if a.nuance == "clarify" and a.follow_up and a.follow_up_ts is not None
    }

    def _speaking_before() -> bool:
        return bool(getattr(speak_pipe, "speaking", False))

    try:
        prev_ts: float | None = None
        # Build one ordered delivery stream: transcript lines + clarify follow-ups.
        stream: list[tuple[float, str, str, Any]] = []  # (ts, speaker, text, ask-or-None)
        for line in meeting.lines:
            key = (line.ts, " ".join(line.text.lower().split()))
            stream.append((line.ts, line.speaker, line.text, ask_by_key.get(key)))
        for ts, a in follow_ups.items():
            # A clarify follow-up is an un-prefixed human reply; arm the pending window first.
            stream.append((ts, a.speaker, a.follow_up, None))
        stream.sort(key=lambda x: x[0])

        armed_for: set[str] = set()
        for ts, speaker, text, ask in stream:
            # Proportional-compressed inter-line wait (relative ordering preserved).
            if prev_ts is not None:
                gap = max(0.0, ts - prev_ts)
                wait = min(max_wait_s, gap * compression)
                if wait > 0:
                    await asyncio.sleep(wait)
            prev_ts = ts

            body = {
                "words": text, "speaker": speaker, "timestamp": ts, "end_of_turn": True,
            }
            # 1) The REAL barge-in reflex FIRST (Law 3 — human control absolute).
            was_speaking = _speaking_before()
            await _cut_speech_on_human_voice(runtime, body, MEETING_ID)
            if was_speaking and not _speaking_before():
                result.barge_cuts += 1
                monitor.mark("barge-in-cut", f"{speaker}: {text[:60]}")

            # If this is a clarify ask, arm the pending-ask window BEFORE its follow-up.
            if ask is not None and ask.nuance == "clarify" and ask.id not in armed_for:
                # (armed after the clarify ask feeds below; handled post-feed)
                pass

            # 2) Feed the engine (spawns the wake turn as a background task; no drain).
            line = TranscriptLine(text=text, speaker=speaker, timestamp=ts, end_of_turn=True)
            if ask is not None:
                result.t_wakes[ask.id] = time.perf_counter()
                monitor.mark("ask-landed", f"[{ask.id}] {ask.kind}: {text[:60]}")
            else:
                monitor.mark("transcript-in", f"{speaker}: {text[:50]}")
            await engine.feed_transcript(line)

            # Arm the follow-up window right after a clarify ask feeds (the runner's seam).
            if ask is not None and ask.nuance == "clarify" and ask.follow_up:
                with contextlib.suppress(Exception):
                    engine.arm_pending_ask()
                armed_for.add(ask.id)

        # Let every in-flight background turn finish — but BOUND it: a single hung SDK
        # stream must never stall the whole run (the generator's 38-min stall taught this).
        monitor.mark("drain", "awaiting all in-flight turns")
        try:
            await asyncio.wait_for(engine.drain(), timeout=drain_timeout_s)
            monitor.mark("drain-done", f"{len(engine.turns)} turns completed")
        except (asyncio.TimeoutError, TimeoutError):
            inflight = tuple(getattr(engine, "_inflight", ()) or ())
            for task in inflight:
                task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(*inflight, return_exceptions=True)
            monitor.mark("drain-timeout",
                         f"drain exceeded {drain_timeout_s:.0f}s — cancelled {len(inflight)} "
                         f"hung turn(s); {len(engine.turns)} completed")
            print(f"[player] WARNING: drain timed out; cancelled {len(inflight)} hung turn(s)")
        with contextlib.suppress(Exception):
            await speak_pipe.aclose()
    finally:
        if sandbox_handle is not None:
            with contextlib.suppress(Exception):
                await sandbox_handle.kill()
            monitor.mark("sandbox", "E2B killed")

    return result
