"""MEETING-IN-A-BOX — the ENTIRE live chain, offline, with the REAL product code.

This is the "simulate the whole live meeting internally" proof the founder asked for. It boots the
REAL chain end to end and fakes ONLY the three genuine vendors, each at its exact seam:

  * E2B cloud   -> ``FakeSandbox`` (in-memory files + a session-host emulator) via the real
                   ``e2b_sandbox_class`` seam. The fake host answers a wake by writing a plausible
                   WAKE_OUT/<id>.json record with a ``say`` — exactly what ``session_host.py`` does.
  * Cartesia    -> a scripted ``synthesize`` that emits real PCM bytes, wired into the REAL
                   ``SpeakPipe`` bound to the REAL ``output_media`` channel (so PCM actually lands on
                   the per-meeting audio channel the Recall bot's page connects to).
  * Recall      -> a ``FakeRoom`` (the RecallTransport verbs) + a REAL-SHAPE transcript webhook.
  * Postgres    -> in-memory fakes at the repo/claim seams (webhook_events, get_by_bot_id, claim).

EVERYTHING between those seams is the real product path:

    POST-shape Recall webhook  ->  webhook_events row  ->  drain_pending_webhooks (server's ONE
    drain)  ->  provision_launch (make_provision_launcher)  ->  provision_meeting (atomic claim +
    _assemble_workroom + provision_workroom)  ->  runtime registered + session wired (pre-wire
    buffer flushed)  ->  MeetingSession.on_line (append + feed MEETING_NOTES + WAKE GATE)  ->
    run_ask (WAKE_IN append, WAKE_OUT poll, $PROXY_WAKE_OUT mirror)  ->  MeetingConnection replay
    ->  SpeakPipe  ->  output_media channel PCM.

A green run proves the ONE thing that has never happened live: a fed line -> wake -> spoken
response, driven exactly as the webhook feed drives it in production. The only thing NOT covered
(by design — it needs live infra) is the real E2B microVM, the real Anthropic subscription round-
trip, and Recall's actual audio ingestion of the channel's PCM.
"""
from __future__ import annotations

import asyncio
import copy
import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# The Recall realtime transcript envelope — the VERBATIM live delivery captured from a real Google
# Meet (fixtures/real_recall_transcript_envelope.json), so every metadata key Recall actually sends
# rides along. THE regression this pins: the outer ``data`` level carries a ``transcript`` metadata
# OBJECT (the recording's transcript resource, not text); a key-existence check in the body unwrap
# stopped there, found no words one level early, and silently dropped EVERY live utterance (the
# exact live failure: Proxy heard perfectly, fed nothing). A synthetic minimal payload missed this;
# the verbatim envelope cannot.
_REAL_ENVELOPE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "real_recall_transcript_envelope.json").read_text()
)


def _transcript_webhook(bot_id: str, words: str, speaker: str, ts: float,
                        *, event: str = "transcript.data") -> dict[str, Any]:
    payload = copy.deepcopy(_REAL_ENVELOPE)
    payload["event"] = event
    payload["data"]["bot"]["id"] = bot_id
    payload["data"]["data"]["words"] = [
        {"text": w, "start_timestamp": {"relative": ts}} for w in words.split()
    ]
    payload["data"]["data"]["participant"]["name"] = speaker
    return payload


def _partial_webhook(bot_id: str, words: str, speaker: str, ts: float) -> dict[str, Any]:
    """A NON-FINAL (partial) transcript webhook — the real-shape envelope with the partial event
    name Recall subscribes to. Used to prove the BUG-3 barge-in reflex fires on human onset."""
    return _transcript_webhook(bot_id, words, speaker, ts, event="transcript.partial_data")


# The record the FAKE session host writes into WAKE_OUT/<id>.json — exactly the shape session_host.py
# produces: the agent judged it WAS addressed, replied by voice (medium 'say'), recorded that intent.
_ANSWER = "Hi — yes, I can hear you. I'm here and ready."


class _FakeFiles:
    """In-memory sandbox filesystem. A genuinely-absent file RAISES (as the real E2B SDK does), so
    the warm driver's poll treats it as 'not written yet'."""

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    async def write(self, path: str, content: str) -> None:
        self._store[path] = content

    async def read(self, path: str) -> str:
        if path not in self._store:
            raise FileNotFoundError(path)
        return self._store[path]


class _FakeCommands:
    """The session-host emulator: the detached launch (``background=True``) brings the host 'up'
    (drops the readiness/heartbeat breadcrumb), and each appended wake line is 'served' into
    WAKE_OUT/<id>.json with a record whose ``say`` echoes the ask — exactly what session_host.py does
    inside the sandbox, so the REAL driver parse + REAL replay + REAL speak run for real."""

    def __init__(self, store: dict[str, str], log: list[str], launch_envs: list[dict[str, str]]) -> None:
        self._store = store
        self._log = log
        self._launch_envs = launch_envs
        from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, WAKE_OUT
        self._ready_file = HOST_READY_FILE
        self._wake_in = WAKE_IN
        self._wake_out = WAKE_OUT
        self._beat = 0.0
        #: Override the served turn record (BUG 2 silent turn = ``sent=[]``); None ⇒ the voice answer.
        self.next_record: dict[str, Any] | None = None

    async def run(self, cmd: str, timeout: int | None = None,
                  envs: dict[str, str] | None = None,
                  background: bool = False) -> SimpleNamespace:
        self._log.append(cmd)
        if background:  # the detached session-host launch → host comes 'up'
            self._launch_envs.append(dict(envs or {}))
            self._beat += 1.0
            self._store[self._ready_file] = str(self._beat)
        if ">>" in cmd and self._wake_in in cmd:  # a wake enqueue → 'serve' it like the warm host
            argv = shlex.split(cmd[cmd.index("printf"):cmd.index(" >>")])
            req = json.loads(argv[-1])
            # Advance the heartbeat each wake so the dead-host watch never trips.
            self._beat += 1.0
            self._store[self._ready_file] = str(self._beat)
            # The served turn record. Default: the agent replied by voice. A SILENT turn (BUG 2) sets
            # ``sent=[]`` — exactly the record the real session_host writes when it emitted the sentinel
            # and delivered nothing — so the box proves a silent turn produces NO PCM.
            record = dict(self.next_record) if self.next_record is not None else {
                "tools": ["to_meeting"],
                "text": _ANSWER,
                "turns": 1,
                "cost_usd": 0.004,
                "error": None,
                "sent": [{"content": _ANSWER, "medium": "say", "to": ""}],
            }
            self._store[f"{self._wake_out}/{req['id']}.json"] = json.dumps(record)
        return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")


class FakeSandbox:
    created_kwargs: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.cmd_log: list[str] = []
        self.launch_envs: list[dict[str, str]] = []
        self.files = _FakeFiles(self._store)
        self.commands = _FakeCommands(self._store, self.cmd_log, self.launch_envs)
        self.sandbox_id = "sbx-in-a-box"
        self.killed = False

    @classmethod
    async def create(cls, **kwargs: object) -> "FakeSandbox":
        FakeSandbox.created_kwargs.append(dict(kwargs))
        return cls()

    async def kill(self) -> None:
        self.killed = True

    async def set_timeout(self, seconds: int) -> None:
        return None


class FakeRoom:
    """The Recall room verbs (creds host-side; unused on the voice happy path)."""

    def __init__(self) -> None:
        self.chats: list[str] = []

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.chats.append(message)

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.chats.append(f"dm:{message}")

    async def mute(self, bot_id: str) -> None:
        return None

    async def unmute(self, bot_id: str) -> None:
        return None


# --- an in-memory Postgres at the exact repo/claim seams the chain touches ----------------------

class _Conn:
    pass


class _Acquire:
    async def __aenter__(self) -> object:
        return _Conn()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeDB:
    """Serves the webhook_events queue + the atomic claim, in memory. instance_id mirrors Database."""

    instance_id = "proc-in-a-box"

    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []
        self.processed: list[Any] = []
        self._next_id = 1

    def acquire(self) -> _Acquire:
        return _Acquire()

    def land(self, payload: dict[str, Any]) -> None:
        """What the webhook route's durable insert-on-conflict does: append a pending row."""
        self.pending.append({"id": self._next_id, "payload": payload})
        self._next_id += 1


def _wire_fakes(monkeypatch: Any, db: FakeDB, *, bot_id: str, meeting_id: str) -> None:
    """Patch every genuine external at its seam; leave ALL orchestration real."""
    import control_plane.provisioner as prov
    import in_meeting.speak as speakmod
    import libs.http.src.http.external as ext
    from libs.db import repos

    # E2B cloud → the in-process fake, through the real e2b_sandbox_class seam.
    monkeypatch.setattr(ext, "e2b_sandbox_class", lambda: FakeSandbox)

    # webhook_events queue (the drain reads/marks these through repos.webhooks).
    async def _list_pending(conn: object) -> list[dict[str, Any]]:
        return list(db.pending)

    async def _mark_processed(conn: object, event_id: Any) -> None:
        db.processed.append(event_id)
        db.pending = [e for e in db.pending if e["id"] != event_id]

    monkeypatch.setattr(repos.webhooks, "list_pending", _list_pending)
    monkeypatch.setattr(repos.webhooks, "mark_processed", _mark_processed)

    # bot → meeting resolve, and repo row → clone url + tenant.
    async def _get_by_bot_id(conn: object, b: str) -> dict[str, Any] | None:
        return {"id": meeting_id, "repo_id": 7, "pinned_sha": None, "tenant_id": "t1"} if b == bot_id else None

    async def _get_repo_by_id(conn: object, repo_id: object) -> dict[str, str]:
        return {"id": repo_id, "tenant_id": "t1", "full_name": "calcom/cal.com"}

    monkeypatch.setattr(repos.meetings, "get_by_bot_id", _get_by_bot_id)
    monkeypatch.setattr(repos.meetings, "get_repo_by_id", _get_repo_by_id)

    # The atomic claim: first caller for a meeting WINS (returns a run id); repeats lose (None).
    claimed: set[str] = set()

    async def _claim_meeting(database: Any, mid: str, op: Any, *, created_by: Any = None) -> Any:
        if mid in claimed:
            return None
        claimed.add(mid)
        return f"run-{mid}"

    monkeypatch.setattr(prov, "claim_meeting", _claim_meeting)
    # _complete_run does a raw conn.execute — the fake conn has none; make it a no-op.
    async def _complete_run(database: Any, run_id: Any) -> None:
        return None

    monkeypatch.setattr(prov, "_complete_run", _complete_run)

    # Cartesia synth → scripted PCM, wired into the REAL SpeakPipe bound to the REAL output_media
    # channel for THIS meeting id (so PCM actually reaches the channel the bot's page connects to).
    from in_meeting.output_media import channel_for
    from in_meeting.speak import build_speak_sink

    async def _synth(text: str) -> Any:
        # 40 bytes of s16le PCM per sentence — even-length, non-empty, plausible.
        yield SimpleNamespace(pcm=b"\x01\x02" * 20)

    def _fake_real_speak_sink(mid: str, **kw: Any) -> Any:
        return build_speak_sink(synthesize=_synth, channel=channel_for(mid), flush_after_s=0.01)

    monkeypatch.setattr(speakmod, "real_speak_sink", _fake_real_speak_sink)


def test_meeting_in_a_box_full_chain(monkeypatch, tmp_path) -> None:
    """The whole live chain, offline: a real-shape Recall transcript webhook drives a fed line all
    the way to spoken PCM on the meeting's output-media channel + a mirrored wake record."""
    FakeSandbox.created_kwargs.clear()

    from control_plane.meeting_runtime import MeetingRuntimeRegistry
    from control_plane.provisioner import make_provision_launcher
    from control_plane.webhooks import drain_pending_webhooks
    from in_meeting import output_media
    from in_meeting.workroom import WAKE_IN

    bot_id = "bot-live-42"
    meeting_id = "m-in-a-box"
    db = FakeDB()
    _wire_fakes(monkeypatch, db, bot_id=bot_id, meeting_id=meeting_id)

    # The deployment origin (relay reachable) + the host-side wake-record mirror dir ($PROXY_WAKE_OUT).
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth-test")
    mirror_dir = tmp_path / "wake_out_mirror"
    monkeypatch.setenv("PROXY_WAKE_OUT", str(mirror_dir))
    # Keep the pre-wire readiness/poll windows snappy for the test (still the real code paths).
    monkeypatch.setenv("PROXY_WARM_PROVISION_WAIT_S", "5")
    monkeypatch.setenv("PROXY_WARM_READY_TIMEOUT_S", "5")

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(db)
        launch = make_provision_launcher(db, registry, timeout_s=30.0)
        # meeting-in-a-box tracks the launched background meeting task so we can await it.
        tasks: set[Any] = set()
        real_ensure = asyncio.ensure_future

        # --- STAGE 1: the FIRST real-shape transcript webhook lands durably (the liveness trigger).
        # It carries the meeting's first spoken words AND is what provisions (no bot.in_call is ever
        # configured live — a transcript for a bot we launched IS the liveness proof).
        first = _transcript_webhook(bot_id, "Hey Proxy can you hear me just say hi back", "Riya", 1.0)
        db.land(first)
        assert len(db.pending) == 1, "the webhook row landed in webhook_events (durable intake)"

        # --- STAGE 2: the server's ONE drain processes it: provision (claim + assemble) via launch.
        await drain_pending_webhooks(db, registry=registry, launch=launch)
        # the row was marked processed (idempotent drain):
        assert db.pending == [] and db.processed, "drain marked the webhook row processed"

        # the background meeting task the launcher spawned — await provision to complete assembly.
        # make_provision_launcher spawns run_meeting_until_end; give it a beat to assemble + wire.
        for _ in range(200):
            rt = registry.get(meeting_id)
            if rt is not None and rt.session is not None and rt.workroom is not None:
                break
            await asyncio.sleep(0.02)
        runtime = registry.get(meeting_id)
        assert runtime is not None, "the drain provisioned + registered the runtime"
        assert runtime.workroom is not None, "the workroom assembled (token + bound repo)"
        assert runtime.session is not None, "the reactive session was wired"

        # --- STAGE 3: the trigger line itself was NOT dropped. provision_meeting ingested it into the
        # pre-wire buffer and wire_session flushed it into on_line — so it fed MEETING_NOTES AND, being
        # an addressed "Hey Proxy…" line, it woke the workroom. Wait for that first wake to deliver.
        for _ in range(400):
            if runtime.session.results:
                break
            await asyncio.sleep(0.02)
        assert runtime.session.results, "the trigger line woke the workroom (wake gate fired)"

        sandbox = runtime.workroom.sandbox
        from in_meeting.workroom import TRANSCRIPT_FILE
        notes = sandbox._store.get(TRANSCRIPT_FILE, "")
        assert "Riya" in notes and "hear me" in notes, "MEETING_NOTES.md got the fed line"

        # --- STAGE 4: run_ask wrote the wake to WAKE_IN and the fake host's record was picked up.
        assert any(">>" in c and WAKE_IN in c for c in sandbox.cmd_log), "run_ask appended to WAKE_IN"
        res = runtime.session.results[0]
        assert res.sent == [{"content": _ANSWER, "medium": "say", "to": ""}], "host record parsed"

        # --- STAGE 5: the response reached the SPEAK PATH — real PCM on the real output-media channel
        # for THIS meeting id (the channel the bot's page connects to at /output-media/<id>/ws).
        channel = output_media.channel_for(meeting_id)
        frames = list(channel._frames)
        assert any(isinstance(f, bytes) and f for f in frames), "PCM audio reached the output-media channel"
        # a speaking-state signal rode too (the orb pulse), proving the real SpeakPipe drove it:
        assert any(isinstance(f, str) and '"speaking"' in f for f in frames)
        # the connection recorded the agent's OWN medium choice (say), replayed over the real conn:
        assert runtime.connection.sent[-1].medium == "say"
        assert runtime.connection.sent[-1].ok is True

        # --- STAGE 6: the wake record was MIRRORED to $PROXY_WAKE_OUT host-side (the monitor tap).
        mirrored = list(mirror_dir.glob("*.json"))
        assert mirrored, "the wake record was mirrored to $PROXY_WAKE_OUT"
        rec = json.loads(mirrored[0].read_text())
        assert rec["sent"][0]["content"] == _ANSWER

        # --- STAGE 7: a follow-up POST-WIRE transcript line (registry.get non-None path) feeds AND a
        # non-addressed line OUTSIDE the follow-up window does NOT wake — the drain's steady-state feed
        # under the name-gate, end to end. (The first turn opened the short follow-up window (F1); this
        # cross-talk arrives well AFTER it closes, so only the name-gate can wake it — and it can't.)
        from control_plane.meeting_session import _FOLLOW_UP_WINDOW_S
        # The follow-up window (F1) is now WALL-clock (anchored past the room's audible horizon), not
        # meeting-clock ts — so advance a controllable wall clock past it to expire it (the first turn
        # opened it; this cross-talk must land AFTER it closes). ``now_fn`` is the session's injectable
        # wall clock (``time.monotonic`` in production); pin it here so real time can jump the window.
        import time as _time
        _wall = _time.monotonic() + _FOLLOW_UP_WINDOW_S + 30.0
        runtime.session.now_fn = lambda: _wall
        late_ts = 1.0 + _FOLLOW_UP_WINDOW_S + 30.0
        cross = _transcript_webhook(bot_id, "great lets move on to the next agenda item", "Riya", late_ts)
        db.land(cross)
        await drain_pending_webhooks(db, registry=registry, launch=launch)
        await runtime.session.drain()
        assert len(runtime.session.results) == 1, "a non-addressed post-wire line (after the window) did NOT wake"

        addressed = _transcript_webhook(bot_id, "Proxy are you still with us", "Riya", late_ts + 1.0)
        db.land(addressed)
        await drain_pending_webhooks(db, registry=registry, launch=launch)
        for _ in range(400):
            if len(runtime.session.results) == 2:
                break
            await asyncio.sleep(0.02)
        assert len(runtime.session.results) == 2, "a post-wire addressed line woke the workroom"

        # --- STAGE 8: meeting end tears the sandbox down (the terminal webhook path).
        end = {"event": "bot.call_ended", "data": {"bot": {"id": bot_id}, "reason": "call_ended"}}
        db.land(end)
        await drain_pending_webhooks(db, registry=registry, launch=launch)
        assert registry.get(meeting_id) is None, "the meeting-end webhook dropped the runtime"
        assert sandbox.killed is True, "the sandbox was torn down at meeting end"

        _ = (tasks, real_ensure)

    try:
        asyncio.run(_run())
    finally:
        # never leak the per-meeting channel across tests
        from in_meeting import output_media
        output_media.close_channel("m-in-a-box")


async def _provision_live(db: "FakeDB", registry: Any, launch: Any, *, bot_id: str,
                          meeting_id: str) -> Any:
    """Drive the real drain+provision path to a live, wired runtime (shared box setup)."""
    from control_plane.webhooks import drain_pending_webhooks

    db.land(_transcript_webhook(bot_id, "Hey Proxy can you hear me just say hi back", "Riya", 1.0))
    await drain_pending_webhooks(db, registry=registry, launch=launch)
    for _ in range(400):
        rt = registry.get(meeting_id)
        if rt is not None and rt.session is not None and rt.workroom is not None and rt.session.results:
            break
        await asyncio.sleep(0.02)
    return registry.get(meeting_id)


def test_meeting_in_a_box_silent_turn_produces_no_pcm(monkeypatch, tmp_path) -> None:
    """BUG 2, end to end in the box: a SILENT turn (the host emitted the sentinel and delivered
    nothing → record ``sent=[]``) reaches the room as ZERO new PCM — the room hears nothing. The
    silent-judgment path never touches the speak channel."""
    FakeSandbox.created_kwargs.clear()
    from control_plane.meeting_runtime import MeetingRuntimeRegistry
    from control_plane.provisioner import make_provision_launcher
    from control_plane.webhooks import drain_pending_webhooks
    from in_meeting import output_media

    bot_id, meeting_id = "bot-silent-1", "m-box-silent"
    db = FakeDB()
    _wire_fakes(monkeypatch, db, bot_id=bot_id, meeting_id=meeting_id)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth-test")
    monkeypatch.setenv("PROXY_WARM_PROVISION_WAIT_S", "5")
    monkeypatch.setenv("PROXY_WARM_READY_TIMEOUT_S", "5")

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(db)
        launch = make_provision_launcher(db, registry, timeout_s=30.0)
        runtime = await _provision_live(db, registry, launch, bot_id=bot_id, meeting_id=meeting_id)
        assert runtime is not None and runtime.session.results, "meeting is live"

        # Now a cross-talk line names 'proxy' incidentally → the host judges silence (sent=[]).
        runtime.workroom.sandbox.commands.next_record = {
            "tools": [], "text": "[SILENT]", "turns": 1, "cost_usd": 0.0, "error": None, "sent": [],
        }
        channel = output_media.channel_for(meeting_id)
        frames_before = len(list(channel._frames))

        db.land(_transcript_webhook(bot_id, "our proxy server keeps dropping connections", "Riya", 9.0))
        await drain_pending_webhooks(db, registry=registry, launch=launch)
        await runtime.session.drain()

        # The silent turn ran (a result recorded) but delivered NOTHING — no new PCM on the channel.
        assert len(runtime.session.results) == 2, "the wake ran"
        assert runtime.session.results[-1].sent == [], "the host judged silence (no intents)"
        frames_after = list(channel._frames)
        new_pcm = [f for f in frames_after[frames_before:] if isinstance(f, bytes) and f]
        assert new_pcm == [], "a silent turn produced NO new PCM — the room heard nothing"

    try:
        asyncio.run(_run())
    finally:
        from in_meeting import output_media as _om
        _om.close_channel(meeting_id)


def test_meeting_in_a_box_partial_barges_in_and_cuts_the_pipe(monkeypatch, tmp_path) -> None:
    """BUG 3, end to end in the box: while Proxy is mid-utterance, a NON-FINAL (partial) transcript
    webhook — the earliest human-onset signal — drives the real barge-in reflex through the drain and
    CUTS the real SpeakPipe (speaking goes False; the cut latch is up), without waking or feeding."""
    FakeSandbox.created_kwargs.clear()
    from control_plane.meeting_runtime import MeetingRuntimeRegistry
    from control_plane.provisioner import make_provision_launcher
    from control_plane.webhooks import drain_pending_webhooks
    from in_meeting import output_media

    bot_id, meeting_id = "bot-partial-1", "m-box-partial"
    db = FakeDB()
    _wire_fakes(monkeypatch, db, bot_id=bot_id, meeting_id=meeting_id)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth-test")
    monkeypatch.setenv("PROXY_WARM_PROVISION_WAIT_S", "5")
    monkeypatch.setenv("PROXY_WARM_READY_TIMEOUT_S", "5")

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(db)
        launch = make_provision_launcher(db, registry, timeout_s=30.0)
        runtime = await _provision_live(db, registry, launch, bot_id=bot_id, meeting_id=meeting_id)
        assert runtime is not None, "meeting is live"

        # Stage Proxy mid-utterance: push audio through the REAL speak pipe so ``speaking`` is True.
        pipe = runtime.speak_pipe
        await pipe.say("I am currently explaining something long ")
        # The pipe is now synthesizing/queued → speaking True (the barge-in guard reads this).
        assert runtime.connection.speak.speaking is True, "Proxy is audibly speaking"

        # A human talks over Proxy — a PARTIAL webhook arrives first (~1s before the final). It must
        # drive the barge-in reflex and cut the pipe NOW.
        db.land(_partial_webhook(bot_id, "wait hold on that is not right", "Riya", 12.0))
        await drain_pending_webhooks(db, registry=registry, launch=launch)

        assert runtime.connection.speak.speaking is False, "the partial cut the active speech"
        assert runtime.connection.cut_latched is True, "the barge-in latch is up"
        # ISSUE 2, end to end: the cut PROPAGATED to the page. The real output-media channel for this
        # meeting must carry a ``{"type":"cut"}`` control frame (the page reads it and stops every
        # scheduled WebAudio source) AND no residual PCM survives — without this the room kept hearing
        # seconds of already-scheduled audio after the host stopped. This is the on-the-wire proof.
        channel = output_media.channel_for(meeting_id)
        wire = list(channel._frames)
        cut_frames = [
            f for f in wire if isinstance(f, str) and json.loads(f).get("type") == "cut"
        ]
        assert cut_frames, "a cut control frame reached the output-media channel (the page clears)"
        assert [f for f in wire if isinstance(f, bytes)] == [], "no residual PCM survives the cut"
        # The partial neither woke nor fed a transcript line (barge-in only).
        assert len(runtime.session.results) == 1, "the partial did NOT wake a new turn"

    try:
        asyncio.run(_run())
    finally:
        from in_meeting import output_media as _om
        _om.close_channel(meeting_id)


def test_meeting_in_a_box_screen_send_lands_a_render_frame(monkeypatch, tmp_path) -> None:
    """SCREEN, end to end in the box: the agent choosing medium='screen' with produced CONTENT drives
    the REAL screen sink, which lands a ``{"screen_html": ...}`` render frame on the real output-media
    channel (the page swaps the orb for a srcdoc iframe) and the connection records an HONEST outcome —
    proof the capability is real, not a silently-dropped frame reported as success (Law 2)."""
    FakeSandbox.created_kwargs.clear()
    from control_plane.meeting_runtime import MeetingRuntimeRegistry
    from control_plane.provisioner import make_provision_launcher
    from in_meeting import output_media

    bot_id, meeting_id = "bot-screen-1", "m-box-screen"
    db = FakeDB()
    _wire_fakes(monkeypatch, db, bot_id=bot_id, meeting_id=meeting_id)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth-test")
    monkeypatch.setenv("PROXY_WARM_PROVISION_WAIT_S", "5")
    monkeypatch.setenv("PROXY_WARM_READY_TIMEOUT_S", "5")

    async def _run() -> None:
        registry = MeetingRuntimeRegistry(db)
        launch = make_provision_launcher(db, registry, timeout_s=30.0)
        runtime = await _provision_live(db, registry, launch, bot_id=bot_id, meeting_id=meeting_id)
        assert runtime is not None, "meeting is live"

        # The agent shows a produced artifact on screen (content, not a URL) via the real connection.
        send = await runtime.connection.to_meeting(
            "<h1>Migration plan</h1><p>Three phases, rolled out weekly.</p>", medium="screen"
        )
        assert send.ok is True and send.medium == "screen"
        assert "showing" in send.detail.lower(), "honest outcome, not a fabricated success"

        # A real screen_html render frame reached the real output-media channel for THIS meeting.
        channel = output_media.channel_for(meeting_id)
        html_frames = [
            json.loads(f)["screen_html"]
            for f in channel._frames
            if isinstance(f, str) and "screen_html" in f
        ]
        assert html_frames, "a screen_html render frame reached the channel (the page renders it)"
        assert "Migration plan" in html_frames[0]

    try:
        asyncio.run(_run())
    finally:
        from in_meeting import output_media as _om
        _om.close_channel(meeting_id)
