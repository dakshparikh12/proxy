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


def _transcript_webhook(bot_id: str, words: str, speaker: str, ts: float) -> dict[str, Any]:
    payload = copy.deepcopy(_REAL_ENVELOPE)
    payload["data"]["bot"]["id"] = bot_id
    payload["data"]["data"]["words"] = [
        {"text": w, "start_timestamp": {"relative": ts}} for w in words.split()
    ]
    payload["data"]["data"]["participant"]["name"] = speaker
    return payload


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
            record = {
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
        # non-addressed line does NOT wake — the drain's steady-state feed, end to end.
        cross = _transcript_webhook(bot_id, "great lets move on to the next agenda item", "Riya", 5.0)
        db.land(cross)
        await drain_pending_webhooks(db, registry=registry, launch=launch)
        await runtime.session.drain()
        assert len(runtime.session.results) == 1, "a non-addressed post-wire line did NOT wake"

        addressed = _transcript_webhook(bot_id, "Proxy are you still with us", "Riya", 6.0)
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
