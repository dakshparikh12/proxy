"""WARM permanent session units — the #1 latency fix.

Focused, offline coverage of the warm-session round-trip that :class:`Workroom.run_ask` now prefers
(and the cold ``claude -p`` FALLBACK it degrades to), plus the in-sandbox ``session_host`` result
shaping. No live sandbox: the E2B seam is a fake that models the wake_in/wake_out file protocol.

The load-bearing invariants proven here:
  * a WARM hit returns the SAME :class:`WorkroomResult` shape the cold ``_parse_stream`` produced
    (tools/text/turns/cost/error/sent) — so ``meeting_session._handle`` is unchanged;
  * a host that never came up (no readiness breadcrumb) FALLS BACK to the cold path (honest degrade);
  * an honest per-turn ``error`` from the host is surfaced as a real result (no cold retry);
  * ``session_host`` shapes a turn's record identically to ``_parse_stream`` (silence, error, salvage).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import shlex
from types import SimpleNamespace
from typing import Any

import pytest


async def _passthru_call(thunk: Any, **kw: Any) -> Any:
    return SimpleNamespace(value=await thunk())


class _WarmSandbox:
    """A fake sandbox that models the warm host: a ``nohup`` launch drops the readiness breadcrumb,
    an appended wake line is 'served' into ``WAKE_OUT/<id>.json`` with a preset record."""

    def __init__(self, record: dict[str, Any] | None, *, ready: bool = True) -> None:
        from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, WAKE_OUT

        self._store: dict[str, str] = {}
        self._record = record
        self._wake_in = WAKE_IN
        self._wake_out = WAKE_OUT
        self._ready_file = HOST_READY_FILE
        self.sandbox_id = "sbx-warm"
        if ready:
            self._store[self._ready_file] = "1"
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
                          envs: dict[str, str] | None = None) -> Any:
                # The driver appends the wake with: printf '%s\n' <shlex-quoted-json> >> WAKE_IN —
                # recover the JSON arg with shlex (the prompt contains embedded quotes, so a naive
                # slice can't undo the quoting), then 'serve' it into WAKE_OUT/<id>.json.
                if ">>" in cmd and outer._wake_in in cmd:
                    argv = shlex.split(cmd[cmd.index("printf"):cmd.index(" >>")])
                    payload = argv[-1] if argv else ""   # printf '%s\n' <arg>  → arg is last token
                    try:
                        req = json.loads(payload)
                    except json.JSONDecodeError:
                        return SimpleNamespace(exit_code=0, stdout="", stderr="")
                    if outer._record is not None:
                        outer._store[f"{outer._wake_out}/{req['id']}.json"] = json.dumps(outer._record)
                return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

        self.files = _Files()
        self.commands = _Cmds()


def test_warm_hit_returns_the_same_workroom_result_shape() -> None:
    """A warm round-trip parses the host's record into the SAME WorkroomResult the cold path yields."""
    from in_meeting.workroom import Workroom

    record = {"tools": ["Read", "to_meeting"], "text": "found it at a.py:3", "turns": 2,
              "cost_usd": 0.02, "error": None,
              "sent": [{"content": "found it at a.py:3", "medium": "say", "to": ""}]}

    async def _run() -> None:
        wr = Workroom(sandbox=_WarmSandbox(record), call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("where is it?")
        assert res.ask == "where is it?"
        assert res.tools == ["Read", "to_meeting"]
        assert res.text == "found it at a.py:3"
        assert res.turns == 2
        assert res.cost_usd == 0.02
        assert res.error is None
        assert res.sent == [{"content": "found it at a.py:3", "medium": "say", "to": ""}]

    asyncio.run(_run())


def test_warm_host_error_is_surfaced_as_a_real_result_no_cold_retry() -> None:
    """An honest per-turn error from the host IS the answer (the session degrades honestly) — it is
    surfaced as a WorkroomResult.error, NOT swallowed into a cold retry."""
    from in_meeting.workroom import Workroom

    record = {"tools": ["Bash"], "text": "", "turns": 1, "cost_usd": 0.0,
              "error": "turn did not complete", "sent": []}

    async def _run() -> None:
        wr = Workroom(sandbox=_WarmSandbox(record), call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("do it")
        assert res.error == "turn did not complete"
        assert res.tools == ["Bash"]

    asyncio.run(_run())


def test_warm_miss_when_host_never_ready_falls_back_to_cold() -> None:
    """No readiness breadcrumb ⇒ the warm attempt gives up fast and run_ask falls back to the cold
    ``claude -p`` path (which the fake serves via /tmp/ask.jsonl) — honest degrade, never a hang."""
    import in_meeting.workroom as wm
    from in_meeting.workroom import Workroom

    class _NoHostSandbox:
        """Warm=True but the host never wrote its readiness file; only the cold path produces output."""

        def __init__(self) -> None:
            self._store: dict[str, str] = {}
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
                              envs: dict[str, str] | None = None) -> Any:
                    if "claude -p" in cmd:  # the COLD path shells out here
                        outer._store["/tmp/ask.jsonl"] = json.dumps(
                            {"type": "result", "result": "cold answer", "total_cost_usd": 0.01})
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        # Shrink the readiness budget so the miss→cold fallback is fast in the test.
        wm._WARM_READY_TIMEOUT_S = 0.5
        wr = Workroom(sandbox=_NoHostSandbox(), call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("proxy, where is it?")
        assert res.text == "cold answer"   # the cold fallback produced the answer

    asyncio.run(_run())


def test_warm_false_uses_cold_directly() -> None:
    """warm=False (no host started) goes straight to the cold path — no readiness wait at all."""
    from in_meeting.workroom import Workroom

    class _ColdSandbox:
        def __init__(self) -> None:
            self._store: dict[str, str] = {}
            self.sandbox_id = "s"
            self.readiness_reads = 0
            outer = self

            class _Files:
                async def write(self, path: str, content: str) -> None:
                    outer._store[path] = content

                async def read(self, path: str) -> str:
                    if path.endswith("_host.ready"):
                        outer.readiness_reads += 1
                    return outer._store.get(path, "") or (
                        json.dumps({"type": "result", "result": "cold", "total_cost_usd": 0.0})
                        if path.endswith("ask.jsonl") else "")

            class _Cmds:
                async def run(self, *a: Any, **k: Any) -> Any:
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        sbx = _ColdSandbox()
        wr = Workroom(sandbox=sbx, call=_passthru_call, token="t", warm=False)
        res = await wr.run_ask("q")
        assert res.text == "cold"
        assert sbx.readiness_reads == 0   # never even checked the warm host

    asyncio.run(_run())


# ── session_host: turn-record shaping parity with _parse_stream ────────────────────


def test_session_host_parse_intents_matches_workroom() -> None:
    """The host's ``_parse_intents`` is byte-for-byte parity with the workroom's — so warm + cold
    produce identical ``sent`` (skips relay-error/malformed lines)."""
    import in_meeting.session_host as sh
    from in_meeting.workroom import _parse_intents as wm_parse

    raw = "\n".join([
        json.dumps({"content": "a", "medium": "say", "to": ""}),
        "not json",
        json.dumps({"content": "drop", "medium": "chat", "relay_error": "boom"}),
        json.dumps({"content": "dm", "medium": "dm", "to": "u1"}),
    ])
    assert sh._parse_intents(raw) == wm_parse(raw)
    assert sh._parse_intents(raw) == [
        {"content": "a", "medium": "say", "to": ""},
        {"content": "dm", "medium": "dm", "to": "u1"},
    ]


class _FakeClient:
    """A stand-in ClaudeSDKClient: replays a preset sequence of SDK messages for one query. If given
    ``records_to``, it writes them to the intents file DURING the query — modelling the MCP server
    recording ``to_meeting`` calls in-turn (after ``_run_turn`` reset the file)."""

    def __init__(self, messages: list[Any], records_to: str | None = None,
                 records: list[dict[str, Any]] | None = None) -> None:
        self._messages = messages
        self._records_to = records_to
        self._records = records or []

    async def query(self, prompt: str) -> None:
        if self._records_to and self._records:
            with pathlib.Path(self._records_to).open("a", encoding="utf-8") as f:
                for rec in self._records:
                    f.write(json.dumps(rec) + "\n")

    async def receive_response(self) -> Any:
        for m in self._messages:
            yield m


def _sdk_msgs() -> Any:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
    return AssistantMessage, ResultMessage, TextBlock, ToolUseBlock


def test_session_host_run_turn_clean_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean turn (assistant tool_use + a result) shapes tools/text/cost/turns + folds intents."""
    import in_meeting.session_host as sh

    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = _sdk_msgs()
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))

    msgs = [
        AssistantMessage(content=[ToolUseBlock(id="1", name="Read", input={}),
                                  TextBlock(text="thinking")], model="m"),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                      num_turns=1, session_id="s", total_cost_usd=0.03, result="the answer"),
    ]
    # The MCP server records the to_meeting call DURING the turn (after _run_turn resets the file).
    client = _FakeClient(msgs, records_to=str(out),
                         records=[{"content": "hi room", "medium": "say", "to": ""}])

    async def _run() -> None:
        rec = await sh._run_turn(client, "q")
        assert rec["tools"] == ["Read"]
        assert rec["text"] == "the answer"
        assert rec["cost_usd"] == 0.03
        assert rec["turns"] == 1
        assert rec["error"] is None
        assert rec["sent"] == [{"content": "hi room", "medium": "say", "to": ""}]

    asyncio.run(_run())


def test_session_host_run_turn_silence_is_not_an_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A turn where the agent chose silence (a result with empty text, no intents recorded / no file)
    is NOT an error — parity with _parse_stream's clean-empty case."""
    import in_meeting.session_host as sh

    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = _sdk_msgs()
    monkeypatch.setattr(sh, "MEETING_OUT", str(tmp_path / "absent.jsonl"))  # never created
    msgs = [
        AssistantMessage(content=[TextBlock(text="Not addressed — staying silent.")], model="m"),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                      num_turns=1, session_id="s", total_cost_usd=0.01, result=""),
    ]

    async def _run() -> None:
        rec = await sh._run_turn(_FakeClient(msgs), "our proxy server is down")
        assert rec["error"] is None
        assert rec["sent"] == []
        assert rec["text"] == ""

    asyncio.run(_run())
