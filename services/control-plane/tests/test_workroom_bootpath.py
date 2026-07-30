"""The WORKROOM boot-path proof — the new brain serves a meeting on the REAL control-plane path.

This exercises the actual cutover wiring end-to-end with only the four genuine externals faked
at their exact seam boundaries:

  * E2B cloud            -> a ``FakeSandbox`` returned through the real ``e2b_sandbox_class`` seam
  * Cartesia+Recall audio -> a ``FakeSpeakPipe`` returned through the real ``real_speak_sink`` seam
  * Postgres repo row     -> ``repos.meetings.get_repo_by_id`` returns the bound repo's full_name
  * Recall action egress  -> a bare transport object (never called on this happy path)

EVERYTHING ELSE IS THE REAL PRODUCT CODE: ``provisioner._assemble_workroom`` (token gate, repo
resolve, honest-degrade, bridge assembly), ``provision_workroom`` (real clone/setup/seed command
sequence through the real ``call_external`` retry+telemetry seam), the real ``Workroom`` methods,
the real ``_parse_stream``, the real ``MeetingBridge`` (transcript-in -> trigger -> wake ->
present -> barge-in), and the real teardown drain+kill.

The meeting is driven exactly as the webhook feed drives it in production: one
``bridge.on_line(Line(...))`` per transcript line (``webhooks.py`` line 366). So a green run here
means: with ``PROXY_USE_WORKROOM=1`` a real join provisions a workroom, streams the transcript
into it, wakes native Claude only on an addressed line, does grounded work, speaks the result
back to the room, and tears the sandbox down — on the real boot path.

The one thing this does NOT cover (by design, and by the founder's call) is a LIVE E2B sandbox +
LIVE Anthropic subscription + LIVE Cartesia/Recall round-trip — that live-vendor smoke is the
founder's to run. Here the vendor edges are faked at their seams; the orchestration is real.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

# The canonical grounded answer a real native-Claude turn would stream back as stream-json:
# two tool_use events (Bash, then Read) then a final result carrying a file:line citation + cost.
_CANNED_STREAM = "\n".join(
    [
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}),
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Read"}]}}),
        json.dumps({"type": "result",
                    "result": "The slugify helper lives at packages/lib/slugify.ts:4 — it "
                              "lowercases then strips non-alphanumerics. I read the actual file "
                              "to confirm.",
                    "total_cost_usd": 0.0123}),
    ]
)


class _FakeFiles:
    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    async def write(self, path: str, content: str) -> None:
        self._store[path] = content

    async def read(self, path: str) -> str:
        return self._store.get(path, "")


class _FakeCommands:
    def __init__(self, store: dict[str, str], log: list[str]) -> None:
        self._store = store
        self._log = log

    async def run(self, cmd: str, timeout: int | None = None,
                  envs: dict[str, str] | None = None) -> SimpleNamespace:
        self._log.append(cmd)
        # A woken turn shells out to native ``claude`` and redirects its stream-json to
        # /tmp/ask.jsonl; the fake "produces" that file so the real reader+parser run for real.
        if "claude -p" in cmd:
            self._store["/tmp/ask.jsonl"] = _CANNED_STREAM
        return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")


class FakeSandbox:
    """An in-process stand-in for e2b ``AsyncSandbox`` — the true external, faked at its seam."""

    created_kwargs: list[dict] = []

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.cmd_log: list[str] = []
        self.files = _FakeFiles(self._store)
        self.commands = _FakeCommands(self._store, self.cmd_log)
        self.sandbox_id = "sbx-fake-boot"
        self.killed = False

    @classmethod
    async def create(cls, **kwargs: object) -> "FakeSandbox":
        FakeSandbox.created_kwargs.append(dict(kwargs))
        return cls()

    async def kill(self) -> None:
        self.killed = True

    async def set_timeout(self, seconds: int) -> None:  # keep-warm surface (unused here)
        return None


class FakeSpeakPipe:
    """An in-process stand-in for the Cartesia->Recall speak pipe (the audio egress external)."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.flushed = 0
        self.cuts = 0

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def flush(self) -> None:
        self.flushed += 1

    async def cut(self) -> None:
        self.cuts += 1


class _FakeAcquire:
    async def __aenter__(self) -> object:
        return SimpleNamespace()  # the conn; the repo lookup is monkeypatched, never touches it

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeDB:
    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


def test_workroom_serves_a_meeting_on_the_real_bootpath(monkeypatch) -> None:
    FakeSandbox.created_kwargs.clear()

    import libs.http.src.http.external as ext
    import in_meeting.speak as speakmod
    from libs.db import repos

    pipe = FakeSpeakPipe()
    # Fake the four externals AT THEIR SEAMS (everything downstream is the real product code):
    monkeypatch.setattr(ext, "e2b_sandbox_class", lambda: FakeSandbox)
    monkeypatch.setattr(speakmod, "real_speak_sink", lambda mid, **kw: pipe)

    async def _fake_repo(conn: object, repo_id: object) -> dict[str, str]:
        return {"id": repo_id, "tenant_id": "t1", "full_name": "calcom/cal.com"}

    monkeypatch.setattr(repos.meetings, "get_repo_by_id", _fake_repo)

    async def _run() -> None:
        from control_plane import provisioner
        from in_meeting.bridge import Line
        from in_meeting.workroom import MAP_FILE, PRIME_FILE, TRANSCRIPT_FILE

        resolved = {"id": "m-boot-1", "repo_id": 42, "pinned_sha": None}
        bridge, speak_pipe, sandbox = await provisioner._assemble_workroom(
            resolved, db=FakeDB(), bot_id="bot-xyz", transport=SimpleNamespace(),
            oauth_token="sk-oauth-test",
        )

        # (1) Assembly succeeded through the REAL _assemble_workroom + REAL provision_workroom.
        assert bridge is not None, "workroom bridge should assemble with a token + bound repo"
        assert sandbox is not None
        assert speak_pipe is pipe
        # provision_workroom built the real setup command for THIS repo (clone into the sandbox):
        setup = "\n".join(sandbox.cmd_log)
        assert "git clone" in setup
        assert "github.com/calcom/cal.com" in setup
        # ...and seeded the orientation files inside the sandbox:
        assert PRIME_FILE in sandbox._store
        assert MAP_FILE in sandbox._store
        assert TRANSCRIPT_FILE in sandbox._store
        # the sandbox was provisioned with a real timeout budget (the create seam ran):
        assert FakeSandbox.created_kwargs and "timeout" in FakeSandbox.created_kwargs[0]

        # (2) Drive the meeting EXACTLY as the webhook feed does — one on_line per transcript line.
        await bridge.on_line(Line(speaker="Alice", text="Let's kick off the release sync", ts=1.0))
        # a non-addressed line must NOT wake the (expensive) workroom:
        assert not bridge.results
        await bridge.on_line(Line(speaker="Bob", text="proxy, where's the slugify helper?", ts=2.0))
        await bridge.drain()  # await the background wake (monitor-while-working)

        # (3) The addressed line woke native Claude, which did grounded work + spoke it back.
        assert len(bridge.results) == 1, "exactly one wake, on the addressed line"
        res = bridge.results[0]
        assert "slugify.ts:4" in res.text          # grounded file:line, from the REAL parse
        assert res.tools == ["Bash", "Read"]        # native tools, ordered, from the REAL parse
        assert res.cost_usd > 0.0                    # cost surfaced from the stream
        # presented to the room via the REAL CartesiaSpeaker -> the (faked) pipe:
        assert any("slugify.ts:4" in s for s in pipe.said)
        assert pipe.flushed >= 1
        # the live transcript was materialized into the sandbox BEFORE the wake read it:
        notes = sandbox._store[TRANSCRIPT_FILE]
        assert "slugify" in notes
        assert "Alice" in notes and "Bob" in notes

        # (4) Teardown drains in-flight work + kills the sandbox (meeting end).
        await bridge.teardown()
        assert sandbox.killed is True

    asyncio.run(_run())


def test_assemble_workroom_degrades_honestly_without_a_token(monkeypatch) -> None:
    """No subscription token -> the meeting still boots, just without a workroom (Law-3 honest
    degrade, never a crash). Returns (None, speak_pipe, None) — the shape the caller expects."""
    import in_meeting.speak as speakmod

    pipe = FakeSpeakPipe()
    monkeypatch.setattr(speakmod, "real_speak_sink", lambda mid, **kw: pipe)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    async def _run() -> None:
        from control_plane import provisioner

        resolved = {"id": "m-boot-2", "repo_id": 42, "pinned_sha": None}
        bridge, speak_pipe, sandbox = await provisioner._assemble_workroom(
            resolved, db=FakeDB(), bot_id="bot-xyz", transport=SimpleNamespace(),
            oauth_token=None,
        )
        assert bridge is None
        assert sandbox is None
        assert speak_pipe is pipe  # the meeting still has a voice channel; just no workroom brain

    asyncio.run(_run())
