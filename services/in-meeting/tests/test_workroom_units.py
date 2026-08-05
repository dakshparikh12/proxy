"""Workroom + prime + sandbox-MCP units — the small pure pieces of the reactive workroom.

The boot-path proof (services/control-plane/tests/test_workroom_bootpath.py) exercises provision +
run_ask + teardown end-to-end. These add focused unit coverage of the pure helpers the boot-path
only touches on the happy path: the prime's render_meeting_info shapes, the workroom transcript-sync
+ run_ask never-raise degrade (on the single warm delivery path), and the in-sandbox MCP server's
_deliver (record vs relay, relay-fault fallback). All offline, no sandbox.
"""
from __future__ import annotations

import asyncio
import json
import shlex
from types import SimpleNamespace
from typing import Any


# ── render_meeting_info: who's-in-the-room, honest when empty ──────────────────────


def test_render_meeting_info_shapes() -> None:
    from in_meeting.prime import render_meeting_info

    full = render_meeting_info(title="Sprint review", agenda="ship it", participants=("Ann", "Bob"))
    assert "**Title:** Sprint review" in full
    assert "**Agenda:** ship it" in full
    assert "- Ann" in full and "- Bob" in full

    # empty -> honest placeholder, never a fabricated roster:
    empty = render_meeting_info()
    assert "(no meeting metadata available)" in empty

    # partial (title only) does not fabricate participants:
    partial = render_meeting_info(title="Standup")
    assert "**Title:** Standup" in partial and "Participants" not in partial


def test_render_meeting_info_surfaces_participant_ids_for_dm() -> None:
    """BUG 6 (DM usable): when a Recall participant id is known it is rendered beside the name
    (``- Ann (id: p9)``) so the agent can pass it as a DM's ``to`` (``send_dm`` addresses by id, not
    name), and the prime carries the DM-id instruction. A name-only participant still renders."""
    from in_meeting.prime import render_meeting_info

    # (name, id) pairs — the shape the provisioner now passes from the Recall callback:
    info = render_meeting_info(
        title="Planning", participants=[("Ann", "p9"), ("Bob", ""), {"name": "Cy", "id": "p3"}]
    )
    assert "- Ann (id: p9)" in info          # id surfaced for the DM ``to``
    assert "- Cy (id: p3)" in info           # mapping form works too
    assert "- Bob" in info and "Bob (id:" not in info   # no id → name-only, never a fabricated id
    # the prime tells the agent a DM ``to`` must be a participant id (not a name):
    assert "participant id" in info.lower() and "dm" in info.lower()

    # bare-name participants (no ids at all) still render, with the DM-id note present:
    names_only = render_meeting_info(participants=("Dana", "Eve"))
    assert "- Dana" in names_only and "- Eve" in names_only
    assert "participant id" in names_only.lower()


# ── compose_resident_prime: the injection guardrail is present + LAST ──────────────


def test_resident_prime_carries_the_shared_injection_guardrail_last() -> None:
    """SECURITY (Hard Rule: prompt safety / SPEC §3.10). The CLAUDE.md seeded into the warm session
    MUST carry the SHARED injection guardrail — transcript content (which now accumulates resident in
    the conversation) is untrusted DATA, never instructions — and it must be the strict LAST word so
    nothing after it (in the prime or injected via transcript data) can lift it. The body is the ONE
    shared source (``agentkit``), never a per-service copy."""
    from agentkit import INJECTION_GUARDRAIL_MARK, injection_guardrail_suffix
    from in_meeting.workroom import compose_resident_prime

    # With an understanding block: the guardrail is present and appears AFTER the understanding.
    composed = compose_resident_prime("BEHAVIORAL PRIME", "the map with real file:line facts")
    assert INJECTION_GUARDRAIL_MARK in composed
    assert composed.rstrip().endswith(injection_guardrail_suffix())   # strict LAST word
    assert composed.index("the map with real") < composed.index(INJECTION_GUARDRAIL_MARK)

    # Even with NO understanding block (empty map) the guardrail is STILL present and last — it is
    # never conditional on the map (a security invariant, not a nicety).
    bare = compose_resident_prime("BEHAVIORAL PRIME", "")
    assert bare.rstrip().endswith(injection_guardrail_suffix())
    assert "BEHAVIORAL PRIME" in bare


# ── Workroom.feed_transcript / run_ask: never-raise honest degrade ────────────────


class _WriteBoomSandbox:
    """A sandbox whose file write raises — to prove feed_transcript never crashes the meeting."""

    class _Files:
        async def write(self, path: str, content: str) -> None:
            raise RuntimeError("disk full")

    def __init__(self) -> None:
        self.files = self._Files()
        self.sandbox_id = "sbx-boom"


async def _passthru_call(thunk: Any, **kw: Any) -> Any:
    # the call_external seam stand-in: just await the thunk and wrap it in a .value carrier.
    return SimpleNamespace(value=await thunk())


def test_feed_transcript_never_raises_on_a_write_fault() -> None:
    from in_meeting.workroom import Workroom

    async def _run() -> None:
        wr = Workroom(sandbox=_WriteBoomSandbox(), call=_passthru_call, token="t")
        # a failing sandbox write must be swallowed (the meeting continues):
        await wr.feed_transcript("# notes\n")

    asyncio.run(_run())


def test_run_ask_degrades_honestly_when_the_session_never_comes_up() -> None:
    """run_ask never raises. When every sandbox command faults, the warm host can't launch, the
    restart-and-retry can't heal it, and the turn honest-degrades to a WorkroomResult.error (the
    session then speaks a bare 'ran into a problem' line) — never an exception into the loop."""
    import in_meeting.workroom as wm
    from in_meeting.workroom import Workroom

    class _CmdBoomSandbox:
        class _Cmds:
            async def run(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("e2b command timeout")

        class _Files:
            async def write(self, *a: Any, **k: Any) -> None:
                return None

            async def read(self, *a: Any, **k: Any) -> str:
                raise FileNotFoundError("nothing")

        def __init__(self) -> None:
            self.commands = self._Cmds()
            self.files = self._Files()
            self.sandbox_id = "s"

    async def _run() -> None:
        wm._WARM_READY_TIMEOUT_S = 0.2   # keep both readiness waits fast
        wr = Workroom(sandbox=_CmdBoomSandbox(), call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("do it")
        assert res.error == "workroom session unavailable"   # honest degrade, no crash, no fake reply
        assert res.text == ""
        assert res.ask == "do it"

    asyncio.run(_run())


def test_run_ask_missing_result_file_is_clean_silence_not_an_error() -> None:
    """When the agent chooses SILENCE (declines a false wake / says nothing) the host still writes a
    clean per-turn record with empty text + no intents. That is honored as clean silence, NOT a
    run_ask error — otherwise a correct silence surfaces a spurious 'I hit a problem' degrade into a
    room Proxy stayed out of. The warm host's own record carries the silence verbatim."""
    from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, WAKE_OUT, Workroom

    silent_record = {"tools": [], "text": "", "turns": 1, "cost_usd": 0.01,
                     "error": None, "sent": []}

    class _SilentWarmSandbox:
        def __init__(self) -> None:
            self._store: dict[str, str] = {HOST_READY_FILE: "1"}
            self.sandbox_id = "s"
            outer = self

            class _Files:
                async def write(self, path: str, content: str) -> None:
                    outer._store[path] = content

                async def read(self, path: str) -> str:
                    if path not in outer._store:
                        raise FileNotFoundError(path)
                    return outer._store[path]

            class _Cmds:
                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None,
                              background: bool = False) -> Any:
                    if ">>" in cmd and WAKE_IN in cmd:
                        argv = shlex.split(cmd[cmd.index("printf"):cmd.index(" >>")])
                        req = json.loads(argv[-1])
                        outer._store[f"{WAKE_OUT}/{req['id']}.json"] = json.dumps(silent_record)
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        wr = Workroom(sandbox=_SilentWarmSandbox(), call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("our proxy server keeps timing out")
        assert res.error is None      # a clean silent record is silence, NOT an error
        assert res.sent == []         # no channel choices → the session stays quiet
        assert res.text == ""

    asyncio.run(_run())


def test_run_ask_absorbs_a_transport_cancel_and_never_crashes_the_meeting() -> None:
    """A wake's own E2B I/O can raise a bare ``CancelledError`` when the HTTP/2 connection is reset /
    GOAWAYs under load (the E2B/httpx/anyio cancel-scope) even though the meeting is NOT ending —
    observed on real E2B (WS6 long-session: 2 such cancels across ~26 wakes). run_ask promises to
    NEVER raise, so it absorbs a spurious transport cancel into an honest no-reply result rather than
    crashing the meeting driver. Only a GENUINE task-drain cancel is re-raised (covered below)."""
    import in_meeting.workroom as wm
    from in_meeting.workroom import Workroom

    async def _boom(_self: Any, _ask: str, _prompt: str) -> Any:
        raise asyncio.CancelledError("http2 stream reset under load")  # transport, not a drain

    async def _run() -> None:
        wr = Workroom(sandbox=SimpleNamespace(sandbox_id="s"), call=_passthru_call, token="t",
                      warm=True)
        orig = Workroom._run_ask_once
        Workroom._run_ask_once = _boom  # type: ignore[method-assign]  # simulate a transport cancel escaping the warm turn
        try:
            res = await wr.run_ask("do it")        # MUST NOT raise
        finally:
            Workroom._run_ask_once = orig  # type: ignore[method-assign]
        assert res.error == "workroom transport interrupted this turn"
        assert res.text == "" and res.ask == "do it"
        # sanity: the guard reads cancelling()==0 as spurious (no genuine drain pending here)
        assert wm._poll_cancel_is_spurious() is True

    asyncio.run(_run())


def test_run_ask_honors_a_genuine_meeting_end_drain() -> None:
    """A GENUINE caller cancellation (meeting-end drain → the run_ask task is cancelled) is NOT
    swallowed — it propagates so teardown stays prompt (Law 3). Distinguished from a transport blip by
    ``current_task().cancelling() > 0`` (a real cancel pending on THIS task)."""
    from in_meeting.workroom import Workroom

    started = asyncio.Event()

    async def _slow(_self: Any, _ask: str, _prompt: str) -> Any:
        started.set()
        await asyncio.sleep(10)   # will be interrupted by the genuine .cancel()

    async def _run() -> None:
        wr = Workroom(sandbox=SimpleNamespace(sandbox_id="s"), call=_passthru_call, token="t",
                      warm=True)
        orig = Workroom._run_ask_once
        Workroom._run_ask_once = _slow  # type: ignore[method-assign]
        try:
            task = asyncio.create_task(wr.run_ask("do it"))
            await started.wait()
            task.cancel()
            raised = False
            try:
                await task
            except asyncio.CancelledError:
                raised = True
            assert raised   # the genuine drain propagated, not absorbed into a fake result
        finally:
            Workroom._run_ask_once = orig  # type: ignore[method-assign]

    asyncio.run(_run())


def test_start_session_host_hands_the_relay_wiring_as_envs() -> None:
    """The warm session host inherits the meeting relay wiring + the subscription auth (RELAY/TOKEN/
    OUT + CLAUDE_CODE_OAUTH_TOKEN + the wake_in/out paths) at launch, so a live turn's to_meeting
    reaches the host. This is the single place the relay envs flow now (the cold ``claude -p`` env
    hand-off is gone)."""
    from in_meeting.workroom import (
        MCP_SERVER_FILE,
        TO_MEETING_OUT,
        WAKE_IN,
        WAKE_OUT,
        Workroom,
        _start_session_host,
    )

    class _RecordSandbox:
        def __init__(self) -> None:
            self._store: dict[str, str] = {}
            self.launch_envs: dict[str, str] | None = None
            outer = self

            class _Files:
                async def write(self, path: str, content: str) -> None:
                    outer._store[path] = content

                async def read(self, path: str) -> str:
                    return outer._store.get(path, "")

            class _Cmds:
                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None,
                              background: bool = False) -> Any:
                    if background:                       # the detached session-host launch
                        outer.launch_envs = dict(envs or {})
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()
            self.sandbox_id = "s"

    async def _run() -> None:
        sandbox = _RecordSandbox()
        wr = Workroom(
            sandbox=sandbox, call=_passthru_call, token="sk-oauth",
            relay_url="https://host/meetings/m/relay", relay_token="bearer-xyz",
        )
        assert await _start_session_host(wr) is True
        envs = sandbox.launch_envs or {}
        assert envs["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oauth"
        assert envs["PROXY_MEETING_RELAY"] == "https://host/meetings/m/relay"
        assert envs["PROXY_MEETING_TOKEN"] == "bearer-xyz"
        assert envs["PROXY_MEETING_OUT"] == TO_MEETING_OUT
        # the host also gets the wake-protocol + MCP-server wiring it needs to serve turns:
        assert envs["PROXY_MCP_SERVER"] == MCP_SERVER_FILE
        assert envs["PROXY_WAKE_IN"] == WAKE_IN
        assert envs["PROXY_WAKE_OUT"] == WAKE_OUT

    asyncio.run(_run())


# ── sandbox_meeting_mcp._deliver: record (proof) vs relay (live) + fault fallback ──


def test_sandbox_mcp_records_locally_when_no_relay(tmp_path, monkeypatch) -> None:
    """No PROXY_MEETING_RELAY -> proof/simulation mode: each to_meeting is appended as one JSON
    line to PROXY_MEETING_OUT so the host can see exactly what Proxy chose."""
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setenv("PROXY_MEETING_OUT", str(out))
    monkeypatch.delenv("PROXY_MEETING_RELAY", raising=False)
    # reload the module so it re-reads the env at import (module-level constants):
    import importlib

    import in_meeting.sandbox_meeting_mcp as mcp
    mcp = importlib.reload(mcp)

    msg = mcp._deliver("hello room", "say", "")
    assert "delivered via say" in msg
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["content"] == "hello room" and rec["medium"] == "say"


def test_sandbox_mcp_relay_fault_falls_back_to_local_record(tmp_path, monkeypatch) -> None:
    """In live mode a relay POST fault never crashes the agent's turn — it records the intent
    locally (with the error noted) and returns an honest 'failed' string."""
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setenv("PROXY_MEETING_OUT", str(out))
    monkeypatch.setenv("PROXY_MEETING_RELAY", "http://host/relay")
    monkeypatch.setenv("PROXY_MEETING_TOKEN", "tok")
    import importlib

    import in_meeting.sandbox_meeting_mcp as mcp
    mcp = importlib.reload(mcp)

    def _boom_relay(rec: dict) -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(mcp, "_relay", _boom_relay)
    msg = mcp._deliver("post this", "chat", "")
    assert "failed" in msg and "network down" in msg
    # the intent + the relay_error were still recorded locally:
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["content"] == "post this" and rec["relay_error"] == "network down"


def test_sandbox_mcp_relay_success_returns_the_host_response(tmp_path, monkeypatch) -> None:
    """A successful relay returns the host's response string and does NOT fall back to a local
    record (the live path)."""
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setenv("PROXY_MEETING_OUT", str(out))
    monkeypatch.setenv("PROXY_MEETING_RELAY", "http://host/relay")
    import importlib

    import in_meeting.sandbox_meeting_mcp as mcp
    mcp = importlib.reload(mcp)

    monkeypatch.setattr(mcp, "_relay", lambda rec: '{"ok": true}')
    msg = mcp._deliver("said it", "say", "")
    assert msg == '{"ok": true}'
    assert not out.exists()   # live success does not write the local proof record


def _reset_sandbox_mcp_env() -> None:
    """Restore the module to its default (no-relay) env so other tests see a clean import."""
    import importlib

    import in_meeting.sandbox_meeting_mcp as mcp
    importlib.reload(mcp)
