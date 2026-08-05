"""WARM permanent session units — the ONE delivery path.

Focused, offline coverage of the warm-session round-trip :class:`Workroom.run_ask` runs (and the
restart-then-retry self-heal it uses on a miss, then the honest degrade), plus the in-sandbox
``session_host`` result shaping. No live sandbox: the E2B seam is a fake that models the
wake_in/wake_out file protocol.

The load-bearing invariants proven here:
  * a WARM hit returns a :class:`WorkroomResult` (tools/text/turns/cost/error/sent) —
    so ``meeting_session._handle`` is unchanged;
  * a host that never came up (no readiness breadcrumb) is RESTARTED and the warm turn RETRIED once;
    if the restart brings it up, the retry answers — otherwise the turn honest-degrades to an error;
  * an honest per-turn ``error`` from the host is surfaced as a real result (no restart/retry);
  * ``session_host`` shapes a turn's record for the driver (silence, error, salvage).
"""
from __future__ import annotations

import asyncio
import contextlib
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


def test_warm_result_surfaces_queued_ms_for_the_queue_latency_battery() -> None:
    """BUG 5: the host records QUEUE LATENCY (how long a wake sat behind an in-flight turn on the
    single-flight warm session); the driver surfaces it on WorkroomResult.queued_ms so the live
    battery can assert the feed→turn-start gap."""
    from in_meeting.workroom import Workroom

    record = {"tools": [], "text": "ok", "turns": 1, "cost_usd": 0.0, "error": None,
              "sent": [], "queued_ms": 31200.0}

    async def _run() -> None:
        wr = Workroom(sandbox=_WarmSandbox(record), call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("q")
        assert res.queued_ms == 31200.0

    asyncio.run(_run())


def test_session_host_serve_stamps_queued_ms_from_queued_at(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BUG 5: the session host computes ``queued_ms`` from the driver's ``queued_at`` enqueue stamp
    — the honest measure of the single-flight queue wait. A missing/old stamp degrades to 0.0."""
    import time as _time

    import in_meeting.session_host as sh

    monkeypatch.setattr(sh, "WAKE_IN", str(tmp_path / "wake_in.jsonl"))
    monkeypatch.setattr(sh, "WAKE_OUT", str(tmp_path / "wake_out"))
    served: list[dict[str, Any]] = []

    async def _fake_run_turn(client: Any, prompt: str) -> dict[str, Any]:
        return {"tools": [], "text": "ok", "turns": 1, "cost_usd": 0.0, "error": None, "sent": []}

    monkeypatch.setattr(sh, "_run_turn", _fake_run_turn)

    def _fake_write(wake_id: str, record: dict[str, Any]) -> None:
        served.append(record)

    monkeypatch.setattr(sh, "_write_result", _fake_write)

    # Enqueue one wake stamped 0.2s ago, then run _serve just long enough to serve it.
    wake_in = pathlib.Path(sh.WAKE_IN)
    wake_in.parent.mkdir(parents=True, exist_ok=True)
    queued_at = _time.time() - 0.2
    wake_in.write_text(json.dumps({"id": "w1", "prompt": "p", "queued_at": queued_at}) + "\n",
                       encoding="utf-8")

    async def _run() -> None:
        task = asyncio.ensure_future(sh._serve(object()))
        for _ in range(50):
            if served:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert served, "the wake was served"
        assert served[0]["queued_ms"] >= 150.0, "queued_ms reflects the ~0.2s the wake sat queued"

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


def test_warm_miss_restarts_the_host_and_the_retry_answers() -> None:
    """No readiness breadcrumb at first ⇒ the warm attempt gives up fast; run_ask RESTARTS the
    session host (which now writes the readiness file + serves the wake) and the RETRY answers —
    one self-heal on the single delivery path, never a hang, never a second engine."""
    import in_meeting.workroom as wm
    from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, WAKE_OUT, Workroom

    record = {"tools": ["Read"], "text": "answered after restart", "turns": 1,
              "cost_usd": 0.01, "error": None, "sent": []}

    class _RestartHealsSandbox:
        """The host is dead until _start_session_host's launch runs; then it goes ready + serves."""

        def __init__(self) -> None:
            self._store: dict[str, str] = {}
            self.sandbox_id = "s"
            self.launches = 0
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
                    # The background launch (from _start_session_host during the restart) brings the
                    # host "up": drop the readiness breadcrumb so the retry's _await_host_ready latches.
                    if background:
                        outer.launches += 1
                        outer._store[HOST_READY_FILE] = "1"
                    # Serve a wake exactly like the live host would (parse the printf'd JSON → WAKE_OUT).
                    if ">>" in cmd and WAKE_IN in cmd:
                        argv = shlex.split(cmd[cmd.index("printf"):cmd.index(" >>")])
                        payload = argv[-1] if argv else ""
                        try:
                            req = json.loads(payload)
                        except json.JSONDecodeError:
                            return SimpleNamespace(exit_code=0, stdout="", stderr="")
                        outer._store[f"{WAKE_OUT}/{req['id']}.json"] = json.dumps(record)
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        # Shrink the readiness budget so the first (failing) wait is fast in the test.
        wm._WARM_READY_TIMEOUT_S = 0.5
        sbx = _RestartHealsSandbox()
        wr = Workroom(sandbox=sbx, call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("proxy, where is it?")
        assert res.text == "answered after restart"   # the restarted host served the retry
        assert sbx.launches == 1                        # the host was (re)launched exactly once

    asyncio.run(_run())


def test_warm_unavailable_after_restart_degrades_honestly() -> None:
    """When the host never comes up — not at first, and not after the restart either — run_ask does
    NOT hang and does NOT fake a reply: it returns an honest ``WorkroomResult.error`` so the session
    speaks a bare degrade. This is the terminal branch of the single delivery path."""
    import in_meeting.workroom as wm
    from in_meeting.workroom import Workroom

    class _DeadHostSandbox:
        """No readiness breadcrumb EVER (even the restart's launch doesn't bring it up)."""

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
                              envs: dict[str, str] | None = None,
                              background: bool = False) -> Any:
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        wm._WARM_READY_TIMEOUT_S = 0.3   # both readiness waits (first + post-restart) stay fast
        wr = Workroom(sandbox=_DeadHostSandbox(), call=_passthru_call, token="t", warm=True)
        res = await wr.run_ask("proxy, where is it?")
        assert res.error == "workroom session unavailable"   # honest degrade, no fake reply
        assert res.text == ""

    asyncio.run(_run())


# ── BLOCKER B2: a crashed/hung host recovers in SECONDS, not the 900s ASK_TIMEOUT_S ────────────


def test_dead_host_midask_recovers_in_seconds_not_the_900s_ceiling() -> None:
    """BLOCKER B2. The host was warm (readiness latched), the wake is enqueued — then the host CRASHES
    (OOM/SIGKILL): no result is ever written and its heartbeat breadcrumb FREEZES. The driver must NOT
    spin the full ASK_TIMEOUT_S (900s = up to 15 min of dead air): it detects the frozen heartbeat
    within the short dead-host budget, restarts the host, and the retry answers — all in ~seconds.

    We assert BOTH: (a) the round-trip returns fast (bounded by a tiny dead-host budget, nowhere near
    900s), and (b) the recovery path (restart → retry answers) actually ran."""
    import in_meeting.workroom as wm
    from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, WAKE_OUT, Workroom

    record = {"tools": ["Read"], "text": "answered after restart", "turns": 1,
              "cost_usd": 0.01, "error": None, "sent": []}

    class _DiesMidAskSandbox:
        """Warm + heartbeat FROZEN (never advances) and NO result for the first wake — a crashed host.
        The restart's background launch relaunches a healthy host: heartbeat starts advancing and the
        retry's wake is served."""

        def __init__(self) -> None:
            self._store: dict[str, str] = {}
            self.sandbox_id = "s"
            self.launches = 0
            self._beat = 0
            self._alive = False  # the ORIGINAL host is dead (crashed); restart brings a live one up
            self._served: set[str] = set()
            outer = self

            class _Files:
                async def write(self, path: str, content: str) -> None:
                    outer._store[path] = content

                async def read(self, path: str) -> str:
                    # The heartbeat breadcrumb: FROZEN while the host is dead (same value every read);
                    # ADVANCING once a live host has been (re)launched.
                    if path == HOST_READY_FILE:
                        if outer._alive:
                            outer._beat += 1
                            return f"beat-{outer._beat}"
                        return "beat-frozen"  # crashed host: value never changes
                    if path not in outer._store:
                        raise FileNotFoundError(path)
                    return outer._store[path]

            class _Cmds:
                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None,
                              background: bool = False) -> Any:
                    if background:                      # the restart's _start_session_host launch
                        outer.launches += 1
                        outer._alive = True             # a healthy host is now up (heartbeat advances)
                        outer._store[HOST_READY_FILE] = "beat-live"
                    if ">>" in cmd and WAKE_IN in cmd:
                        argv = shlex.split(cmd[cmd.index("printf"):cmd.index(" >>")])
                        payload = argv[-1] if argv else ""
                        try:
                            req = json.loads(payload)
                        except json.JSONDecodeError:
                            return SimpleNamespace(exit_code=0, stdout="", stderr="")
                        # Only the LIVE (restarted) host serves the wake; the crashed one never does.
                        if outer._alive:
                            outer._store[f"{WAKE_OUT}/{req['id']}.json"] = json.dumps(record)
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        # Tiny budgets so the test is fast AND proves the point: the dead-host detection is what ends
        # the wait, and it is a SMALL fraction of the 900s ceiling.
        wm._DEAD_HOST_TIMEOUT_S = 0.4
        wm._WARM_POLL_S = 0.02
        wm._WARM_READY_TIMEOUT_S = 0.5
        assert wm.ASK_TIMEOUT_S >= 900.0     # the ceiling is unchanged (long WORKING turns still allowed)
        sbx = _DiesMidAskSandbox()
        wr = Workroom(sandbox=sbx, call=_passthru_call, token="t", warm=True)
        wr._host_ready = True                # the host WAS warm before it crashed (readiness latched)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        res = await wr.run_ask("proxy, install the go toolchain and build")
        elapsed = loop.time() - t0
        assert res.text == "answered after restart"   # recovered: restarted host served the retry
        assert sbx.launches == 1                        # the dead host was restarted exactly once
        # THE B2 GUARANTEE: recovered in seconds, NOT the 900s ceiling (no 15-min dead air).
        assert elapsed < 30.0, f"dead-host recovery took {elapsed:.1f}s — must be seconds, not 900s"

    asyncio.run(_run())


def test_long_working_turn_is_not_mistaken_for_dead_while_heartbeat_advances() -> None:
    """The dead-host budget must NOT cut short a genuinely-long WORKING turn. As long as the host's
    heartbeat keeps advancing (it beats even mid-turn), the wait is allowed to run past the dead-host
    budget — the result lands only AFTER several dead-host windows would have elapsed on a frozen host,
    yet the turn is served, not aborted. ASK_TIMEOUT_S stays the ceiling for real work."""
    import in_meeting.workroom as wm
    from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, WAKE_OUT, Workroom

    record = {"tools": ["Read", "Bash"], "text": "the deep result", "turns": 6,
              "cost_usd": 0.2, "error": None, "sent": []}

    class _LongTurnSandbox:
        """A LIVE host on a long turn: the heartbeat advances on every read; the result is withheld
        until several dead-host windows' worth of polls have passed, then served."""

        def __init__(self, reads_before_result: int) -> None:
            self._store: dict[str, str] = {HOST_READY_FILE: "beat-0"}
            self.sandbox_id = "s"
            self._beat = 0
            self._reads_before_result = reads_before_result
            self._result_reads = 0
            self._pending_id: str | None = None
            outer = self

            class _Files:
                async def write(self, path: str, content: str) -> None:
                    outer._store[path] = content

                async def read(self, path: str) -> str:
                    if path == HOST_READY_FILE:
                        outer._beat += 1                 # ALIVE: heartbeat always advances
                        return f"beat-{outer._beat}"
                    # The result file: withheld until the host has "worked" long enough, then served.
                    if outer._pending_id and path.endswith(f"{outer._pending_id}.json"):
                        outer._result_reads += 1
                        if outer._result_reads >= outer._reads_before_result:
                            return json.dumps(record)
                        raise FileNotFoundError(path)   # not done yet
                    if path not in outer._store:
                        raise FileNotFoundError(path)
                    return outer._store[path]

            class _Cmds:
                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None,
                              background: bool = False) -> Any:
                    if ">>" in cmd and WAKE_IN in cmd:
                        argv = shlex.split(cmd[cmd.index("printf"):cmd.index(" >>")])
                        payload = argv[-1] if argv else ""
                        try:
                            req = json.loads(payload)
                        except json.JSONDecodeError:
                            return SimpleNamespace(exit_code=0, stdout="", stderr="")
                        outer._pending_id = str(req["id"])
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        wm._DEAD_HOST_TIMEOUT_S = 0.2
        wm._WARM_POLL_S = 0.01
        # The result only appears after ~30 result-reads — far more polls than fit in ONE dead-host
        # window (proving a frozen host would have been aborted long before), yet the LIVE host's
        # advancing heartbeat keeps the wait alive until the turn completes.
        sbx = _LongTurnSandbox(reads_before_result=30)
        wr = Workroom(sandbox=sbx, call=_passthru_call, token="t", warm=True)
        wr._host_ready = True
        res = await wr.run_ask("proxy, write the full migration and verify it")
        assert res.text == "the deep result"   # a long working turn is served, NOT cut short
        assert res.error is None

    asyncio.run(_run())


# ── session_host: turn-record shaping (tools/text/cost/turns/error/sent) ───────────


def test_session_host_parse_intents_shapes_the_recorded_channel_choices() -> None:
    """The host's ``_parse_intents`` turns the in-sandbox MCP server's recorded ``to_meeting`` lines
    into ``[{content, medium, to}]`` — the agent's OWN channel choices this turn — skipping
    relay-error / malformed lines (the local record is best-effort)."""
    import in_meeting.session_host as sh

    raw = "\n".join([
        json.dumps({"content": "a", "medium": "say", "to": ""}),
        "not json",
        json.dumps({"content": "drop", "medium": "chat", "relay_error": "boom"}),
        json.dumps({"content": "dm", "medium": "dm", "to": "u1"}),
    ])
    assert sh._parse_intents(raw) == [
        {"content": "a", "medium": "say", "to": ""},
        {"content": "dm", "medium": "dm", "to": "u1"},
    ]


def test_session_host_heartbeat_advances_the_readiness_breadcrumb(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BLOCKER B2 (host side). The readiness breadcrumb is also the heartbeat: ``_beat`` rewrites it
    with a fresh value, and ``_heartbeat`` keeps advancing it forever. A live host's breadcrumb value
    therefore CHANGES over time — the exact signal the driver watches to tell alive from dead."""
    import in_meeting.session_host as sh

    out = tmp_path / "wake_out"
    monkeypatch.setattr(sh, "WAKE_OUT", str(out))
    out.mkdir()

    sh._beat()
    first = (out / "_host.ready").read_text(encoding="utf-8")
    assert first  # a beat was written

    async def _run() -> None:
        monkeypatch.setattr(sh, "_HEARTBEAT_S", 0.01)
        task = asyncio.create_task(sh._heartbeat())
        await asyncio.sleep(0.05)   # let several ticks land
        task.cancel()
        return (out / "_host.ready").read_text(encoding="utf-8")

    later = asyncio.run(_run())
    assert later != first, "the heartbeat must ADVANCE the breadcrumb (a frozen value = dead host)"


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
    is NOT an error — the agent chose silence, which the driver must honor as a clean empty turn."""
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


class _SlowClient:
    """A ClaudeSDKClient stand-in that SLEEPS ``delay`` s before it begins yielding messages — so a
    turn can outlast the opener budget (modelling the model thinking hard before its first token).
    ``deltas`` (if given) are yielded as ``content_block_delta`` stream events BEFORE the trailing
    messages, modelling the model streaming its own prose. All say-outputs land in the intents file."""

    def __init__(self, messages: list[Any], *, delay: float = 0.0,
                 deltas: list[str] | None = None) -> None:
        self._messages = messages
        self._delay = delay
        self._deltas = deltas or []

    async def query(self, prompt: str) -> None:
        return None

    async def receive_response(self) -> Any:
        if self._delay:
            await asyncio.sleep(self._delay)
        for text in self._deltas:
            yield SimpleNamespace(event={"type": "content_block_delta",
                                         "delta": {"type": "text_delta", "text": text}})
        for m in self._messages:
            yield m


def test_session_host_opener_fires_when_work_started_and_silent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I1 SAFETY NET — PRIMARY TRIGGER. The moment the model COMMITS TO WORK (its first real tool call)
    and stays silent past the short after-tool grace, EXACTLY ONE canned acknowledgment is spoken — a
    genuinely-working turn is never dead air (Law 5, be present). This is the new work-gated trigger
    (not pure wall-clock): the opener fires because a tool started, so it only ever fires on a turn
    that is demonstrably off doing multi-step work."""
    import in_meeting.session_host as sh

    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = _sdk_msgs()
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 0.02)  # tiny after-tool grace
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 999.0)  # prove it's the TOOL, not the backstop

    # A silent assistant turn that CALLS A TOOL (Bash) with no spoken prose, then a result — the
    # watchdog must fire off the tool-started signal, not the (huge) hard floor.
    class _ToolThenSilent:
        async def query(self, prompt: str) -> None:
            return None

        async def receive_response(self) -> Any:
            yield AssistantMessage(content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
                                   model="m")
            await asyncio.sleep(0.3)  # >> after-tool grace + poll ⇒ the watchdog fires
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="s", total_cost_usd=0.0, result="")

    async def _run() -> None:
        rec = await sh._run_turn(_ToolThenSilent(), "rename Foo to Bar across the repo")
        says = [s for s in rec["sent"] if s["medium"] == "say"]
        assert says == [{"content": sh._OPENER_TEXT, "medium": "say", "to": ""}], \
            "exactly one canned opener fires once work has started and the turn is still silent"
        assert rec["deliver_at"] > 0, "the room heard the opener ⇒ deliver_at is set"

    asyncio.run(_run())


def test_session_host_opener_fires_on_hard_floor_when_no_tool(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I1 SAFETY NET — NO-TOOL BACKSTOP. A rare pure-reasoning heavy answer that emits NO tool for a
    long time must still not leave dead air: past ``_OPENER_HARD_FLOOR_S`` of total silence the canned
    opener fires. (The floor is set FAR above the normal judged-answer TTFT band in production, so it
    never pre-empts an ordinary answer/decline — see the regression test below.)"""
    import in_meeting.session_host as sh

    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = _sdk_msgs()
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 999.0)  # no tool will start; irrelevant here
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 0.02)  # tiny backstop; the turn outlasts it

    msgs = [ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                          num_turns=1, session_id="s", total_cost_usd=0.0, result="")]
    client = _SlowClient(msgs, delay=0.1)  # silent, no tool, outlasts the backstop

    async def _run() -> None:
        rec = await sh._run_turn(client, "reason hard about X and answer")
        says = [s for s in rec["sent"] if s["medium"] == "say"]
        assert says == [{"content": sh._OPENER_TEXT, "medium": "say", "to": ""}], \
            "the no-tool backstop fires exactly one opener on prolonged silence"
        assert rec["deliver_at"] > 0

    asyncio.run(_run())


def test_session_host_opener_not_fired_on_direct_answer_or_silent_decline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I1 REGRESSION (the whole point of the work-gated opener). A turn that calls NO tool — a direct
    answer OR a silent cross-talk decline — must NEVER get a spurious canned opener, even when its
    first token lags past the old wall-clock budget (measured 6-12s on a less-familiar repo / hard
    judgment). Before this fix a 4s wall-clock net spoke 'On it — give me a moment.' in front of the
    answer AND on turns that then chose SILENCE (blurting filler on a line not addressed to Proxy).
    Here the model is silent for well past the after-tool grace with NO tool call; the opener must NOT
    fire within the normal band (the hard floor is far away)."""
    import in_meeting.session_host as sh

    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = _sdk_msgs()
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 0.02)  # would fire fast IF a tool had started
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 999.0)  # far away — models the real 15s floor

    # A silent-then-answers turn with NO tool call (a judged direct answer). It "thinks" 0.1s (past
    # the after-tool grace) before streaming its own words — the opener must stay suppressed.
    client = _SlowClient(
        [ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                       num_turns=1, session_id="s", total_cost_usd=0.01, result="done")],
        delay=0.1, deltas=["The Context class is at context.go:61. "],
    )

    async def _run() -> None:
        rec = await sh._run_turn(client, "where is Context defined?")
        says = [s["content"] for s in rec["sent"] if s["medium"] == "say"]
        assert sh._OPENER_TEXT not in says, \
            "no spurious opener on a no-tool direct answer / silent decline (the regression fix)"
        assert says == ["The Context class is at context.go:61."], "only the model's own words"

    asyncio.run(_run())


def test_session_host_opener_suppressed_when_model_speaks_within_budget(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I1 NO DOUBLE-SPEAK. When the model streams its OWN opener within the budget, the canned
    acknowledgment is SUPPRESSED — the safety net never talks over the model. The recorded say is the
    model's words only; the canned text never appears."""
    import in_meeting.session_host as sh

    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = _sdk_msgs()
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    # Tiny backstop so the watchdog WOULD fire absent the model's words — proving real suppression,
    # not a trivially-far floor. The model's own opener streams immediately (delay=0) and must win.
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 0.05)
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 0.05)

    # The model streams its own opener as a complete sentence (flushed to voice), then a result.
    msgs = [ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                          num_turns=1, session_id="s", total_cost_usd=0.01, result="done")]
    client = _SlowClient(msgs, delay=0.0, deltas=["Sure, let me take a look. "])

    async def _run() -> None:
        rec = await sh._run_turn(client, "where is the retry wired?")
        says = [s["content"] for s in rec["sent"] if s["medium"] == "say"]
        assert says == ["Sure, let me take a look."], "only the model's own opener is spoken"
        assert sh._OPENER_TEXT not in says, "the canned opener must be suppressed (no double-speak)"

    asyncio.run(_run())


def test_session_host_opener_suppressed_when_model_mid_stream_at_budget(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I1 NO DOUBLE-SPEAK — MID-STREAM. The real double-speak seen on the understanding-tour ask:
    the model has STARTED streaming (deltas arriving, ttft set) but its FIRST sentence hasn't closed
    yet when the budget elapses. The net must still suppress — the model's own words are a beat away,
    so firing the canned filler would speak right on top of them. Here the model streams a partial
    clause WITH NO sentence terminator until after the budget, so ``spoke`` is still False at the
    budget but ``ttft``/``say_buf`` are set — the suppression must key off active streaming, not only
    a closed sentence."""
    import in_meeting.session_host as sh

    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = _sdk_msgs()
    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    # Tiny backstop so the watchdog WOULD be due during the mid-stream gap — proving the suppression
    # keys off active streaming (ttft/say_buf), not only a closed sentence.
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 0.05)
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 0.05)

    # The model emits a partial clause (no terminator) BEFORE the budget, then closes the sentence
    # only in a later delta that arrives after the budget has elapsed — modelling TTFT < budget but
    # first-sentence-close > budget.
    msgs = [ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                          num_turns=1, session_id="s", total_cost_usd=0.01, result="done")]

    class _MidStreamClient:
        async def query(self, prompt: str) -> None:
            return None

        async def receive_response(self) -> Any:
            # First a partial clause (no sentence end) → ttft set, say_buf filling, spoke still False.
            yield SimpleNamespace(event={"type": "content_block_delta",
                                         "delta": {"type": "text_delta", "text": "Sure, here's the "}})
            await asyncio.sleep(0.15)  # > budget: the watchdog runs during this gap
            # Then the sentence closes.
            yield SimpleNamespace(event={"type": "content_block_delta",
                                         "delta": {"type": "text_delta", "text": "lay of the land. "}})
            for m in msgs:
                yield m

    async def _run() -> None:
        rec = await sh._run_turn(_MidStreamClient(), "give me the tour")
        says = [s["content"] for s in rec["sent"] if s["medium"] == "say"]
        assert sh._OPENER_TEXT not in says, \
            "the canned opener must be suppressed while the model is mid-stream (no double-speak)"
        assert says == ["Sure, here's the lay of the land."], "only the model's own words are spoken"

    asyncio.run(_run())


# ── BUG 1: never speak markdown syntax or a raw URL ──────────────────────────────


def test_sanitize_for_voice_drops_urls_and_markdown_syntax() -> None:
    """BUG 1 unit. The voice sanitizer strips markdown syntax and never lets a URL through to TTS —
    it does NOT try to read markdown beautifully, only ensures the room never hears literal `**` or a
    forecast.weather.gov query string read out character by character (the live failure)."""
    import in_meeting.session_host as sh

    assert sh._sanitize_for_voice("It's **82°F** right now") == "It's 82°F right now"
    assert sh._sanitize_for_voice("`Context` is at context.go:61") == "Context is at context.go:61"
    # A markdown link keeps only the human label; the URL is never spoken.
    assert sh._sanitize_for_voice(
        "See the [NWS 7-Day Forecast](https://forecast.weather.gov/MapClick.php?x=1&y=2)."
    ) == "See the NWS 7-Day Forecast."
    # A bare URL is dropped (never read out as characters).
    assert sh._sanitize_for_voice(
        "Sources: https://forecast.weather.gov/MapClick.php?CityName=Santa+Clara"
    ) == "Sources:"
    # A leading markdown bullet/heading marker is dropped (no spoken "dash"/"hash").
    assert sh._sanitize_for_voice("- first point") == "first point"
    assert sh._sanitize_for_voice("## Summary") == "Summary"
    # A chunk that is ONLY a URL sanitizes to empty → the caller skips it.
    assert sh._sanitize_for_voice("https://example.com/a/b?c=1") == ""
    # Plain prose is untouched (no over-eager stripping of ordinary punctuation / underscores in code).
    assert sh._sanitize_for_voice("The value is 3.14 exactly.") == "The value is 3.14 exactly."
    assert sh._sanitize_for_voice("call some_func(x) now") == "call some_func(x) now"


def test_run_turn_never_streams_a_raw_url_or_markdown_to_voice(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BUG 1 integration. The live trace: the weather answer streamed `**82°F**` and a raw
    forecast.weather.gov URL and TTS spoke them verbatim. After the fix the streamed voice carries the
    sanitized gist only — no markdown syntax, no URL — proving the physics net at the text→TTS boundary
    (``_deliver_say``). The agent's own words still reach the room; only the syntax/URL is dropped."""
    import in_meeting.session_host as sh

    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 999.0)
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 999.0)

    client = _SlowClient(
        [ResultMessageT()],
        delay=0.0,
        deltas=[
            "It's **82°F** in Santa Clara right now. ",
            "Sources: https://forecast.weather.gov/MapClick.php?x=1 more later. ",
        ],
    )

    async def _run() -> None:
        rec = await sh._run_turn(client, "what's the weather?")
        says = [s["content"] for s in rec["sent"] if s["medium"] == "say"]
        assert says == [
            "It's 82°F in Santa Clara right now.",
            "Sources: more later.",
        ], "voice carries the sanitized gist — no markdown syntax, no raw URL"
        assert not any("http" in s or "**" in s for s in says)

    asyncio.run(_run())


# ── BUG 2: a stay-silent turn speaks NOTHING (sentinel-gated) ─────────────────────


def _silent_deltas(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> Any:
    import in_meeting.session_host as sh

    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    # Production shape: the no-tool hard floor sits ABOVE the (fast) silent-decision band — the
    # sentinel streams first, standing the opener down (``ttft``/``say_buf``/``silent`` are set the
    # instant the first token lands). Modelled here with a floor well above the sentinel's arrival.
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 5.0)
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 5.0)
    return sh


def test_silent_sentinel_turn_speaks_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BUG 2 core. The live trace: a cross-talk wake streamed its reasoning ("...not addressing me.
    Staying silent.") and TTS spoke it. After the fix the model emits the `[SILENT]` sentinel; the
    delivery layer suppresses ALL voice (zero say deliveries) while the record still captures the text
    for the trace, and NO canned opener fires either."""
    sh = _silent_deltas(monkeypatch, tmp_path)

    # The model streams the sentinel (possibly split across deltas, with trailing reasoning it may jot
    # AFTER the line — none of which is spoken). It "thinks" 0.1s first, past the tiny opener floor.
    client = _SlowClient(
        [ResultMessageT(result="[SILENT]\nDaksh means a technical proxy, not me.")],
        delay=0.1,
        deltas=["[SIL", "ENT]\n", "Daksh means a technical proxy, not me."],
    )

    async def _run() -> None:
        rec = await sh._run_turn(client, "My proxy stopped helping.")
        says = [s for s in rec["sent"] if s["medium"] == "say"]
        assert says == [], "a silent-sentinel turn produces ZERO say deliveries (no reasoning spoken)"
        assert sh._OPENER_TEXT not in [s.get("content") for s in rec["sent"]], \
            "the opener watchdog must not fire a spoken opener on a silent turn"

    asyncio.run(_run())


def test_silent_sentinel_is_case_and_whitespace_robust(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BUG 2 robustness. A stray capital or surrounding whitespace must still be recognized as the
    sentinel — never leak a spoken 'staying silent' into the room."""
    sh = _silent_deltas(monkeypatch, tmp_path)
    client = _SlowClient([ResultMessageT(result="  [silent]  ")], delay=0.0,
                         deltas=["  [silent]  "])

    async def _run() -> None:
        rec = await sh._run_turn(client, "our proxy server is down")
        assert [s for s in rec["sent"] if s["medium"] == "say"] == []

    asyncio.run(_run())


def test_normal_turn_after_sentinel_prefix_still_speaks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BUG 2 no-regression. A normal answer that merely STARTS with a bracket (e.g. "[cal.com] uses…")
    must NOT be swallowed as silent — once the streamed content diverges from the sentinel, the held
    buffer is spoken. A plain answer is unchanged."""
    sh = _silent_deltas(monkeypatch, tmp_path)
    monkeypatch.setattr(sh, "_OPENER_HARD_FLOOR_S", 999.0)  # don't let the opener confuse this test
    monkeypatch.setattr(sh, "_OPENER_AFTER_TOOL_S", 999.0)
    client = _SlowClient([ResultMessageT(result="done")], delay=0.0,
                         deltas=["[cal", ".com] uses Prisma for the ORM. "])

    async def _run() -> None:
        rec = await sh._run_turn(client, "what ORM does it use?")
        says = [s["content"] for s in rec["sent"] if s["medium"] == "say"]
        assert says == ["[cal.com] uses Prisma for the ORM."], \
            "a normal answer that starts with a bracket is spoken (not swallowed as silent)"

    asyncio.run(_run())


def ResultMessageT(result: str = "done") -> Any:
    """A minimal success ResultMessage for the BUG-1/2 tests (kept local so the sdk import stays in
    ``_sdk_msgs`` for the older tests)."""
    _, ResultMessage, _, _ = _sdk_msgs()
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s", total_cost_usd=0.0, result=result)


def test_spurious_transport_cancel_on_the_result_read_does_not_orphan_the_turn() -> None:
    """LIVE-PATH RELIABILITY (found on real E2B under load): the E2B envd RPC layer converts a
    connect-level CANCELED — an HTTP/2 stream reset / a connection dropped mid-request under load —
    into ``asyncio.CancelledError`` (``e2b/envd/rpc.py``). If that spurious cancel from a POLL read
    killed the wait, the wake would be ABANDONED even though the warm session keeps running and writes
    its result — the room hears nothing on a turn that SUCCEEDED server-side. Here the result read
    raises ``CancelledError`` on its first poll (no genuine cancellation pending on the task), then the
    record is served: the driver must SWALLOW the spurious cancel, keep polling, and deliver."""
    from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, WAKE_OUT, Workroom

    record = {"tools": ["Read"], "text": "delivered despite a transport blip", "turns": 1,
              "cost_usd": 0.0, "error": None, "sent": []}

    class _FlakyReadSandbox:
        """Warm + serving, but the FIRST read of the result path raises a spurious CancelledError
        (the E2B transport blip) before the record is available on later polls."""

        def __init__(self) -> None:
            self._store: dict[str, str] = {HOST_READY_FILE: "beat-0"}
            self.sandbox_id = "s"
            self._beat = 0
            self._result_reads = 0
            self._out_path: str | None = None
            outer = self

            class _Files:
                async def write(self, path: str, content: str) -> None:
                    outer._store[path] = content

                async def read(self, path: str) -> str:
                    if path == HOST_READY_FILE:
                        outer._beat += 1
                        return f"beat-{outer._beat}"   # alive: heartbeat advances every poll
                    if outer._out_path and path == outer._out_path:
                        outer._result_reads += 1
                        if outer._result_reads == 1:
                            # The spurious E2B transport cancel on the first result poll. No genuine
                            # cancellation is pending on this task, so the driver must treat it as
                            # "not written yet" and keep polling — NOT abandon the wake.
                            raise asyncio.CancelledError
                    if path not in outer._store:
                        raise FileNotFoundError(path)
                    return outer._store[path]

            class _Cmds:
                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None,
                              background: bool = False) -> Any:
                    if ">>" in cmd and WAKE_IN in cmd:
                        argv = shlex.split(cmd[cmd.index("printf"):cmd.index(" >>")])
                        payload = argv[-1] if argv else ""
                        try:
                            req = json.loads(payload)
                        except json.JSONDecodeError:
                            return SimpleNamespace(exit_code=0, stdout="", stderr="")
                        outer._out_path = f"{WAKE_OUT}/{req['id']}.json"
                        outer._store[outer._out_path] = json.dumps(record)
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        import in_meeting.workroom as wm
        wm._WARM_POLL_S = 0.02
        sbx = _FlakyReadSandbox()
        wr = Workroom(sandbox=sbx, call=_passthru_call, token="t", warm=True)
        wr._host_ready = True
        res = await wr.run_ask("proxy, what's the fix?")
        assert res.text == "delivered despite a transport blip"  # recovered, NOT orphaned
        assert sbx._result_reads >= 2  # the first (cancelled) poll was swallowed; a later one delivered

    asyncio.run(_run())


def test_genuine_task_cancellation_still_propagates_through_a_poll_read() -> None:
    """The other side of the guard: a GENUINE cancellation of the wake task (e.g. meeting-end drain)
    must still propagate — we only swallow a SPURIOUS transport cancel (no cancellation pending on the
    task). Here the surrounding task is really cancelled while run_ask waits on the result poll; the
    CancelledError must NOT be swallowed (cooperative cancellation is honored)."""
    from in_meeting.workroom import HOST_READY_FILE, WAKE_IN, Workroom

    class _NeverServesSandbox:
        """Warm + alive (heartbeat advances) but NEVER writes a result — the wake will wait forever
        until the task is cancelled from outside."""

        def __init__(self) -> None:
            self._store: dict[str, str] = {HOST_READY_FILE: "beat-0"}
            self.sandbox_id = "s"
            self._beat = 0
            outer = self

            class _Files:
                async def write(self, path: str, content: str) -> None:
                    outer._store[path] = content

                async def read(self, path: str) -> str:
                    if path == HOST_READY_FILE:
                        outer._beat += 1
                        return f"beat-{outer._beat}"
                    raise FileNotFoundError(path)  # result never appears

            class _Cmds:
                async def run(self, cmd: str, timeout: int | None = None,
                              envs: dict[str, str] | None = None,
                              background: bool = False) -> Any:
                    return SimpleNamespace(exit_code=0, stdout="DONE", stderr="")

            self.files = _Files()
            self.commands = _Cmds()

    async def _run() -> None:
        import in_meeting.workroom as wm
        wm._WARM_POLL_S = 0.02
        wr = Workroom(sandbox=_NeverServesSandbox(), call=_passthru_call, token="t", warm=True)
        wr._host_ready = True
        task = asyncio.create_task(wr.run_ask("proxy, do it"))
        await asyncio.sleep(0.1)          # let it enter the result-poll loop
        task.cancel()                      # a GENUINE cancellation (meeting-end drain)
        with pytest.raises(asyncio.CancelledError):
            await task                      # must propagate, not be swallowed

    asyncio.run(_run())


# ── ACCEPTANCE (SPEC §3): the transcript is RESIDENT + CACHED, not file-read ───────────────────
#
# The spec: "the transcript accumulates in the agent's cached conversation as it happens → the agent
# just knows the whole meeting, cheap (only the delta per wake is fresh)." Proven here two ways that
# together are the acceptance bar:
#   1. Each wake's FRESH input is only the delta since the last wake + the ask (no recon window, no
#      "go read ./MEETING_NOTES.md for older context" direction) — a pure check on the wake prompt.
#   2. At a LATER wake the agent RECALLS EARLY transcript content with ZERO file-read tool calls —
#      recall comes from the resident conversation cache, not a Read. Modelled with a fake SDK client
#      that (faithfully to the real warm ClaudeSDKClient) ACCUMULATES every query into an in-context
#      conversation across wakes; a later wake answers about an early line purely from that in-memory
#      history and calls NO Read/Grep/Bash/Glob.


def test_wake_prompt_inlines_only_the_delta_and_never_directs_a_transcript_file_read() -> None:
    """ACCEPTANCE 1 — the wake carries ONLY the fresh delta + the ask, and NEVER tells the agent to
    read a transcript file for older context (recall is resident, not a file). This is the exact
    deviation the spec forbids: no recon window, no './MEETING_NOTES.md for older context' pointer."""
    from in_meeting.workroom import Workroom

    delta = "[Ann] the auth token lives in settings.py\n[Bob] and it rotates hourly"
    prompt = Workroom._wake_prompt("proxy, where's the auth token?", delta)

    # The fresh delta is inlined verbatim (this — not a re-read — is how the room reaches the cache):
    assert "the auth token lives in settings.py" in prompt
    assert "proxy, where's the auth token?" in prompt
    # The DEVIATION is gone: nothing points the agent at a transcript file for recall/older context.
    assert "MEETING_NOTES" not in prompt
    assert "older context" not in prompt.lower()
    assert "older meeting history" not in prompt.lower()
    # It affirmatively tells the agent it already remembers the room (resident), no re-read needed:
    lowered = prompt.lower()
    assert "remember" in lowered or "already know" in lowered

    # And with NO delta (a burst of wakes on one line) the prompt still stands — no empty block, and
    # still no file-read direction (the whole meeting is already resident from prior wakes).
    empty = Workroom._wake_prompt("proxy?", "")
    assert "MEETING_NOTES" not in empty


def _recall_answer(prompt: str, conversation: list[str]) -> str | None:
    """The fake model's recall: it can answer the auth-token question ONLY if the fact is already in
    its in-context conversation (a prior wake's delta) — i.e. resident, not re-read. Returns the
    answer text, or ``None`` if the fact isn't in memory (which the real model would then have to go
    find with a tool). Deliberately keyed to whether the FACT is resident, so the test's zero-tool
    assertion means 'recalled from cache', not 'guessed'."""
    if "where's the auth token" not in prompt.lower():
        return None
    resident = "\n".join(conversation)
    if "the auth token lives in settings.py" in resident:
        return "It's in settings.py — recalled that from earlier."
    return None


class _ResidentCacheClient:
    """A ClaudeSDKClient stand-in that models the ONE load-bearing property of the warm session: the
    conversation PREFIX is cached and accumulates across wakes (proven on the real client:
    cache_read grows turn-over-turn). Every ``query`` prompt is appended to an in-memory
    ``conversation`` (= the resident cache). On a wake it RECALLS from that accumulated conversation
    and answers with pure prose — calling NO Read/Grep/Bash/Glob — whenever the fact is already
    resident. If the fact were NOT resident it would (like the real model) have to reach for a file
    tool; here we prove it never has to, because the earlier delta put the fact in the cache."""

    def __init__(self) -> None:
        self.conversation: list[str] = []

    async def query(self, prompt: str) -> None:
        # The warm session accumulates each wake into the cached conversation — this is the resident
        # transcript growing turn-over-turn (the whole point of inlining only the delta).
        self.conversation.append(prompt)

    async def receive_response(self) -> Any:
        AssistantMessage, ResultMessage, TextBlock, _ToolUse = _sdk_msgs()
        answer = _recall_answer(self.conversation[-1], self.conversation)
        if answer is not None:
            # Stream the recalled answer as text deltas (spoken) — and crucially emit NO tool call.
            for chunk in (answer[: len(answer) // 2], answer[len(answer) // 2:]):
                yield SimpleNamespace(event={"type": "content_block_delta",
                                             "delta": {"type": "text_delta", "text": chunk}})
            yield AssistantMessage(content=[TextBlock(text=answer)], model="m")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="s", total_cost_usd=0.01, result=answer)
        else:
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="s", total_cost_usd=0.0, result="")


def test_later_wake_recalls_early_transcript_from_the_resident_cache_with_zero_reads(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ACCEPTANCE 2 (the core proof). An EARLY wake inlines the delta that carries a fact ('the auth
    token lives in settings.py'); that delta enters the warm session's cached conversation. A MUCH
    LATER wake — after intervening unrelated deltas — asks about that early fact and the agent RECALLS
    it correctly while calling ZERO file-read tools (Read/Grep/Bash/Glob). Recall came from the
    resident cache the accumulated deltas built, NOT a per-wake MEETING_NOTES.md read — exactly SPEC
    §3. Runs the REAL warm-session turn loop (``session_host._run_turn``); only the SDK client (the
    caching substrate) is faked, and it is faithful to the proven cache-grows-per-turn behaviour."""
    import in_meeting.session_host as sh
    from in_meeting.workroom import Workroom

    out = tmp_path / "to_meeting.jsonl"
    monkeypatch.setattr(sh, "MEETING_OUT", str(out))
    client = _ResidentCacheClient()

    async def _run() -> None:
        # WAKE 1 (early): the delta carries the fact. The wake is a normal ask, NOT about the token —
        # the fact just rides along in the inlined delta and lands in the cache.
        early = Workroom._wake_prompt(
            "proxy, say hi",
            "[Ann] the auth token lives in settings.py\n[Bob] sounds good",
        )
        rec1 = await sh._run_turn(client, early)
        assert not [t for t in rec1["tools"] if t in {"Read", "Grep", "Bash", "Glob"}]

        # Intervening wakes with UNRELATED deltas — the meeting moves on; the fact is now 'early'.
        for filler in ("[Cy] let's talk about the roadmap", "[Dee] and the Q3 numbers"):
            await sh._run_turn(client, Workroom._wake_prompt("proxy, noted", filler))

        # WAKE 4 (much later): ask about the EARLY fact. The delta this turn does NOT contain it —
        # the ONLY way to answer is from the resident cache built by wake 1.
        later = Workroom._wake_prompt(
            "proxy, where's the auth token?",
            "[Ann] quick question for you",  # this delta does NOT restate the fact
        )
        # The later wake's own delta must NOT re-carry the early fact (else recall would be trivial):
        assert "the auth token lives in settings.py" not in later
        rec4 = await sh._run_turn(client, later)

        # RECALL is correct AND came from cache: the answer names settings.py, and ZERO file-read
        # tools were used to get it (no Read/Grep/Bash/Glob) — resident recall, not a file read.
        assert "settings.py" in rec4["text"]
        read_tools = [t for t in rec4["tools"] if t in {"Read", "Grep", "Bash", "Glob"}]
        assert read_tools == [], f"recall must be from the resident cache, not a file read: {read_tools}"
        # And the fact is provably in the resident conversation the deltas accumulated (the cache):
        assert any("the auth token lives in settings.py" in turn for turn in client.conversation)

    asyncio.run(_run())
