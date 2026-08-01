"""Workroom + prime + sandbox-MCP units — the small pure pieces of the reactive workroom.

The boot-path proof (services/control-plane/tests/test_workroom_bootpath.py) exercises provision +
run_ask + teardown end-to-end. These add focused unit coverage of the pure helpers the boot-path
only touches on the happy path: the stream-json parser's edge handling, the prime's
render_meeting_info shapes, the workroom transcript-sync + run_ask never-raise degrade, and the
in-sandbox MCP server's _deliver (record vs relay, relay-fault fallback). All offline, no sandbox.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any


# ── _parse_stream: claude stream-json -> ordered tools + final text + cost ────────


def test_parse_stream_extracts_tools_text_and_cost_in_order() -> None:
    from in_meeting.workroom import _parse_stream

    raw = "\n".join(
        [
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "tool_use", "name": "Grep"}]}}),
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "tool_use", "name": "Read"},
                                                {"type": "text", "text": "thinking"}]}}),
            json.dumps({"type": "result", "result": "found it at a.py:3",
                        "total_cost_usd": 0.05}),
        ]
    )
    res = _parse_stream("where is it?", raw)
    assert res.tools == ["Grep", "Read"]        # ordered, only tool_use blocks
    assert res.text == "found it at a.py:3"
    assert res.cost_usd == 0.05
    assert res.turns == 2                          # two assistant events
    assert res.ask == "where is it?"


def test_parse_stream_skips_malformed_lines_and_flags_abnormal_termination() -> None:
    """Non-JSON lines are skipped (not fatal). ABNORMAL TERMINATION: assistant turns ran but NO
    ``result`` event arrived (crash/OOM) and NO intents were recorded -> an honest ``error`` is set
    so the session degrades instead of going silent on a task that needed a response."""
    from in_meeting.workroom import _parse_stream

    raw = "\n".join(
        [
            "not json at all",
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}),
            "{ half a line",
        ]
    )
    res = _parse_stream("q", raw)
    assert res.tools == ["Bash"]
    assert res.text == ""
    assert res.cost_usd == 0.0
    assert res.error == "turn did not complete"   # crashed mid-turn with nothing delivered
    assert res.sent == []

    # a wholly empty stream (no assistant turns at all) is a clean empty result, NOT an error:
    empty = _parse_stream("q", "")
    assert empty.tools == [] and empty.text == "" and empty.cost_usd == 0.0
    assert empty.error is None


def test_parse_stream_does_not_salvage_internal_prose_on_abnormal_exit() -> None:
    """BUG-7 (soft Law 2): an abnormally-terminated turn (assistant prose but NO ``result`` event)
    must NOT recover its last assistant prose into ``text`` — that prose is internal scratchpad the
    agent did NOT choose to say to the room, so surfacing it would put words in Proxy's mouth. ``text``
    stays empty; ``error`` is still flagged so the session speaks a bare honest apology instead."""
    from in_meeting.workroom import _parse_stream

    raw = "\n".join(
        [
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Drafting the fix..."}]}}),
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "tool_use", "name": "Edit"}]}}),
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "text",
                                                 "text": "Wrote the migration; validating on Postgres"}]}}),
            # (no result event — killed at the timeout ceiling)
        ]
    )
    res = _parse_stream("implement the fix", raw)
    assert res.text == ""                              # internal prose is NOT spoken to the room
    assert res.error == "turn did not complete"        # still honestly incomplete → bare apology
    assert res.tools == ["Edit"]


def test_parse_stream_no_result_but_recorded_intents_is_not_an_error() -> None:
    """An abnormal-looking stream (assistant turns, no ``result``) is NOT flagged an error when the
    agent DID record ``to_meeting`` intents — those intents carry the turn, so the session replays
    them rather than degrading."""
    from in_meeting.workroom import _parse_stream

    raw = json.dumps({"type": "assistant",
                      "message": {"content": [{"type": "tool_use", "name": "to_meeting"}]}})
    intents = json.dumps({"ts": 1.0, "content": "here you go", "medium": "chat", "to": ""})
    res = _parse_stream("q", raw, intents)
    assert res.error is None
    assert res.sent == [{"content": "here you go", "medium": "chat", "to": ""}]


def test_parse_stream_folds_in_recorded_intents_and_skips_relay_errors() -> None:
    """A clean turn's recorded intents are parsed onto ``result.sent`` (the agent's OWN channel
    choices in the no-relay path); malformed / relay-error lines are skipped (best-effort record)."""
    from in_meeting.workroom import _parse_stream

    raw = json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.0})
    intents = "\n".join([
        json.dumps({"ts": 1.0, "content": "first", "medium": "say", "to": ""}),
        "not json",
        json.dumps({"ts": 2.0, "content": "dropped", "medium": "chat", "relay_error": "boom"}),
        json.dumps({"ts": 3.0, "content": "dm hi", "medium": "dm", "to": "u1"}),
    ])
    res = _parse_stream("q", raw, intents)
    assert res.text == "done"
    assert res.sent == [
        {"content": "first", "medium": "say", "to": ""},
        {"content": "dm hi", "medium": "dm", "to": "u1"},
    ]


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


def test_run_ask_returns_an_honest_error_result_when_the_command_faults() -> None:
    """run_ask never raises — a sandbox command fault becomes WorkroomResult.error (the session
    then speaks an honest 'ran into a problem' line), never an exception into the loop."""
    from in_meeting.workroom import Workroom

    class _CmdBoomSandbox:
        class _Cmds:
            async def run(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("e2b command timeout")

        class _Files:
            async def write(self, *a: Any, **k: Any) -> None:
                return None

            async def read(self, *a: Any, **k: Any) -> str:
                return ""

        def __init__(self) -> None:
            self.commands = self._Cmds()
            self.files = self._Files()
            self.sandbox_id = "s"

    async def _run() -> None:
        wr = Workroom(sandbox=_CmdBoomSandbox(), call=_passthru_call, token="t")
        res = await wr.run_ask("do it")
        assert res.error is not None and "timeout" in res.error
        assert res.text == ""
        assert res.ask == "do it"

    asyncio.run(_run())


def test_run_ask_missing_intent_file_is_clean_silence_not_an_error() -> None:
    """When the agent chooses SILENCE (declines a false wake / says nothing) it never calls
    to_meeting, so $PROXY_MEETING_OUT is never created and its read RAISES. That must be treated as
    'no intents' (clean silence), NOT a run_ask error — otherwise a correct silence surfaces a
    spurious 'I hit a problem' degrade into a room Proxy stayed out of."""
    from in_meeting.workroom import Workroom

    class _SilentSandbox:
        class _Cmds:
            async def run(self, *a: Any, **k: Any) -> Any:
                return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

        class _Files:
            async def write(self, *a: Any, **k: Any) -> None:
                return None

            async def read(self, path: str) -> str:
                if path.endswith("ask.jsonl"):
                    # a clean turn: assistant declined in prose, then a normal result event
                    return "\n".join([
                        json.dumps({"type": "assistant",
                                    "message": {"content": [{"type": "text",
                                                             "text": "Not addressed — staying silent."}]}}),
                        json.dumps({"type": "result", "result": "", "total_cost_usd": 0.01}),
                    ])
                raise FileNotFoundError("path '/tmp/to_meeting.jsonl' does not exist")

        def __init__(self) -> None:
            self.commands = self._Cmds()
            self.files = self._Files()
            self.sandbox_id = "s"

    async def _run() -> None:
        wr = Workroom(sandbox=_SilentSandbox(), call=_passthru_call, token="t")
        res = await wr.run_ask("our proxy server keeps timing out")
        assert res.error is None      # a missing intent file is silence, NOT an error
        assert res.sent == []         # no channel choices → the session stays quiet
        assert res.text == ""

    asyncio.run(_run())


def test_run_ask_hands_the_relay_wiring_as_envs(monkeypatch) -> None:
    """run_ask launches native claude with the meeting MCP config and the relay envs
    (RELAY/TOKEN/OUT + the subscription auth) so a live turn's to_meeting reaches the host."""
    from in_meeting.workroom import MCP_CONFIG_FILE, TO_MEETING_OUT, Workroom

    class _RecordSandbox:
        class _Files:
            def __init__(self, store: dict[str, str]) -> None:
                self._store = store

            async def write(self, path: str, content: str) -> None:
                self._store[path] = content

            async def read(self, path: str) -> str:
                # the ask stream has a result event; the to_meeting record is empty (no relay
                # intents recorded in this env-wiring check) — so only the stream feeds the parser.
                if path.endswith("ask.jsonl"):
                    return json.dumps({"type": "result", "result": "ok", "total_cost_usd": 0.0})
                return ""

        def __init__(self) -> None:
            self._store: dict[str, str] = {}
            self.cmds: list[str] = []
            self.envs: list[dict[str, str]] = []
            self.files = self._Files(self._store)
            self.sandbox_id = "s"

            class _Cmds:
                def __init__(self, outer: Any) -> None:
                    self._outer = outer

                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None) -> Any:
                    self._outer.cmds.append(cmd)
                    self._outer.envs.append(dict(envs or {}))
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.commands = _Cmds(self)

    async def _run() -> None:
        sandbox = _RecordSandbox()
        wr = Workroom(
            sandbox=sandbox, call=_passthru_call, token="sk-oauth",
            relay_url="https://host/meetings/m/relay", relay_token="bearer-xyz",
        )
        res = await wr.run_ask("the ask")
        assert res.text == "ok"
        ask_cmd = next(c for c in sandbox.cmds if "claude -p" in c)
        assert f"--mcp-config {MCP_CONFIG_FILE}" in ask_cmd
        envs = sandbox.envs[sandbox.cmds.index(ask_cmd)]
        assert envs["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oauth"
        assert envs["PROXY_MEETING_RELAY"] == "https://host/meetings/m/relay"
        assert envs["PROXY_MEETING_TOKEN"] == "bearer-xyz"
        assert envs["PROXY_MEETING_OUT"] == TO_MEETING_OUT

    asyncio.run(_run())


def test_cold_command_resets_to_meeting_out_before_the_run() -> None:
    """BUG 5 (cold double-send): the in-sandbox MCP server only APPENDS to TO_MEETING_OUT, so
    without a per-turn reset cold wake N would re-read wakes 1..N-1's intents and re-deliver them.
    The cold command must ``rm -f`` the intent log FIRST — so ``sent`` is exactly THIS turn's
    intents (mirrors the warm host's per-turn reset)."""
    from in_meeting.workroom import TO_MEETING_OUT, Workroom

    class _RecordSandbox:
        class _Files:
            async def write(self, *a: Any, **k: Any) -> None:
                return None

            async def read(self, path: str) -> str:
                if path.endswith("ask.jsonl"):
                    return json.dumps({"type": "result", "result": "ok", "total_cost_usd": 0.0})
                return ""

        def __init__(self) -> None:
            self.files = self._Files()
            self.sandbox_id = "s"
            self.cmds: list[str] = []

            class _Cmds:
                def __init__(self, outer: Any) -> None:
                    self._outer = outer

                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None) -> Any:
                    self._outer.cmds.append(cmd)
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.commands = _Cmds(self)

    async def _run() -> None:
        sandbox = _RecordSandbox()
        wr = Workroom(sandbox=sandbox, call=_passthru_call, token="t")  # warm=False → cold path
        await wr.run_ask("the ask")
        ask_cmd = next(c for c in sandbox.cmds if "claude -p" in c)
        reset = f"rm -f {TO_MEETING_OUT}"
        # the reset is present AND lands BEFORE the claude -p run (so this turn starts with a clean log):
        assert reset in ask_cmd
        assert ask_cmd.index(reset) < ask_cmd.index("claude -p")

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
