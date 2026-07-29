"""Acceptance battery for SANDBOX — the warm per-meeting sandbox + code-execution access.

``in_meeting.sandbox`` gives the agent the LAST piece of its access surface: the
ability to EXECUTE code in a real, isolated E2B sandbox, exposed as COMPOSABLE
MCP tools (run a command, write a file, read a file) it chooses to call — the
same recipe as ``meeting_control.build_meeting_control_server`` and premeeting's
``RepoContext.build_server``. The ENGINE never decides to run anything (Law 4);
the AGENT composes these tools for heavy/code work; this module owns only the
pipe. Every handler is NEVER-THROW (Hard Rule 6): a sandbox fault is an
``is_error`` result, never a raised exception.

Deterministic and offline: the provision backend and the sandbox handle are
FAKES that record calls (never a real E2B round-trip — the controller runs the
real-E2B smoke); the tools are invoked through the REAL mcp ``CallToolRequest``
path, exactly as the SDK drives them. The four AC groups:

1. ``provision_sandbox(backend=fake_create)`` calls the backend with the CURATED
   env (exactly what the caller passed, never the process env, never an api_key)
   and returns the backend's sandbox handle;
2. ``build_sandbox_server(fake_sandbox)`` → an ``McpSdkServerConfig`` named
   ``sandbox``; each tool drives the matching sandbox method with the args and
   returns the result (stdout/stderr/exit_code for run_command, etc.);
3. never-throw — a raising sandbox method becomes an ``is_error`` result
   (parametrized across the three tools);
4. ``SANDBOX_TOOLS`` names EXACTLY the three fully-qualified tools.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from in_meeting.sandbox import (
    SANDBOX_TOOLS,
    SERVER_NAME,
    TOOL_BASENAMES,
    build_sandbox_server,
    provision_sandbox,
)

# ── fakes: a call-recording sandbox + a faulting sandbox (never a real E2B wire) ──


class FakeCommandResult:
    """The e2b ``CommandResult`` shape (stdout/stderr/exit_code) — never a real run."""

    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class FakeCommands:
    """Records every ``run(cmd)``; returns a canned CommandResult."""

    def __init__(self, result: FakeCommandResult | None = None) -> None:
        self.calls: list[str] = []
        self.result = result if result is not None else FakeCommandResult()

    async def run(self, cmd: str) -> FakeCommandResult:
        self.calls.append(cmd)
        return self.result


class FakeFiles:
    """Records writes/reads over an in-memory store — the e2b ``files`` verb shapes."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.reads: list[str] = []
        self.store: dict[str, str] = {}

    async def write(self, path: str, data: str) -> dict[str, str]:
        self.writes.append((path, data))
        self.store[path] = data
        return {"path": path}

    async def read(self, path: str) -> str:
        self.reads.append(path)
        return self.store[path]


class FakeSandbox:
    """The e2b ``AsyncSandbox`` surface this toolbelt touches (``commands`` + ``files``)."""

    def __init__(self, result: FakeCommandResult | None = None) -> None:
        self.commands = FakeCommands(result)
        self.files = FakeFiles()


class RaisingCommands:
    async def run(self, cmd: str) -> Any:
        raise RuntimeError("e2b 502")


class RaisingFiles:
    async def write(self, path: str, data: str) -> Any:
        raise RuntimeError("e2b 502")

    async def read(self, path: str) -> Any:
        raise RuntimeError("e2b 502")


class RaisingSandbox:
    """Every sandbox verb raises — the vendor-fault half of the never-throw boundary."""

    def __init__(self) -> None:
        self.commands = RaisingCommands()
        self.files = RaisingFiles()


class FakeCommandExit(Exception):
    """The e2b ``CommandExitException`` duck-shape: an exception CARRYING the result
    (stdout/stderr/exit_code) — how the real SDK reports a non-zero exit code."""

    def __init__(self, stdout: str, stderr: str, exit_code: int) -> None:
        super().__init__(f"Command exited with code {exit_code} and error:\n{stderr}")
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class ExitRaisingCommands:
    """``run`` raises the non-zero-exit exception, exactly as the installed e2b SDK does."""

    def __init__(self, exc: FakeCommandExit) -> None:
        self.exc = exc

    async def run(self, cmd: str) -> Any:
        raise self.exc


async def _call(server: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a mounted tool through the REAL mcp CallToolRequest path (as the SDK drives it)."""
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call", params=mt.CallToolRequestParams(name=tool_name, arguments=dict(args))
    )
    res = await handler(req)
    text = res.root.content[0].text
    if getattr(res.root, "isError", False):
        return {"__error__": text}
    return dict(json.loads(text))


async def _mounted_tool_names(server: Any) -> list[str]:
    """The tool names the server advertises, via the REAL ListToolsRequest path."""
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.ListToolsRequest]
    res = await handler(mt.ListToolsRequest(method="tools/list"))
    return [t.name for t in res.root.tools]


# ── AC1: provision_sandbox drives the injectable backend with the curated env ──


@pytest.mark.asyncio
async def test_provision_calls_backend_with_curated_env_and_returns_the_handle() -> None:
    seen: dict[str, Any] = {}
    handle = object()

    async def fake_create(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return handle

    got = await provision_sandbox(
        backend=fake_create,
        env={"PIP_INDEX_URL": "https://pypi.org/simple"},
        metadata={"meeting_id": "m-1"},
    )

    assert got is handle
    assert seen["envs"] == {"PIP_INDEX_URL": "https://pypi.org/simple"}
    assert seen["metadata"] == {"meeting_id": "m-1"}
    # Default-deny egress: the network-policy kwarg is THREADED to the create call.
    assert seen["allow_internet_access"] is False
    # The E2B key rides the settings/env surface INSIDE the seam — never passed (or
    # logged) by this module.
    assert "api_key" not in seen


@pytest.mark.asyncio
async def test_provision_curates_the_env_to_exactly_what_was_passed() -> None:
    """No env given → the backend sees an EMPTY env — never the process environment."""
    seen: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return object()

    await provision_sandbox(backend=fake_create)

    assert seen["envs"] == {}
    assert seen["metadata"] == {}


@pytest.mark.asyncio
async def test_provision_threads_an_explicit_network_allow_override() -> None:
    seen: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return object()

    await provision_sandbox(backend=fake_create, allow_internet_access=True)

    assert seen["allow_internet_access"] is True


# ── AC2: build → sdk server config; each tool drives the matching sandbox verb ──


@pytest.mark.asyncio
async def test_build_returns_sdk_server_named_sandbox_advertising_the_three_tools() -> None:
    server = build_sandbox_server(FakeSandbox())
    assert server["type"] == "sdk"
    assert server["name"] == SERVER_NAME == "sandbox"
    assert await _mounted_tool_names(server) == list(TOOL_BASENAMES)


@pytest.mark.asyncio
async def test_run_command_runs_in_the_sandbox_and_returns_stdout_stderr_exit_code() -> None:
    sandbox = FakeSandbox(FakeCommandResult(stdout="3 passed\n", stderr="", exit_code=0))
    server = build_sandbox_server(sandbox)

    out = await _call(server, "run_command", {"command": "pytest -q"})

    assert "__error__" not in out
    assert out == {"stdout": "3 passed\n", "stderr": "", "exit_code": 0}
    assert sandbox.commands.calls == ["pytest -q"]


@pytest.mark.asyncio
async def test_run_command_reports_a_nonzero_exit_as_a_result_not_a_fault() -> None:
    """The installed e2b SDK raises ``CommandExitException`` (carrying the result) on a
    non-zero exit. A failing command is a RESULT the agent composes with (Law 2 — spoken
    plainly), never an opaque tool fault."""
    sandbox = FakeSandbox()
    sandbox.commands = ExitRaisingCommands(  # type: ignore[assignment]
        FakeCommandExit(stdout="", stderr="AssertionError: boom", exit_code=1)
    )
    server = build_sandbox_server(sandbox)

    out = await _call(server, "run_command", {"command": "pytest -q"})

    assert "__error__" not in out
    assert out == {"stdout": "", "stderr": "AssertionError: boom", "exit_code": 1}


@pytest.mark.asyncio
async def test_write_file_writes_the_content_at_the_path() -> None:
    sandbox = FakeSandbox()
    server = build_sandbox_server(sandbox)

    out = await _call(server, "write_file", {"path": "/home/user/probe.py", "content": "print(1)\n"})

    assert "__error__" not in out
    assert out == {"written": True, "path": "/home/user/probe.py"}
    assert sandbox.files.writes == [("/home/user/probe.py", "print(1)\n")]


@pytest.mark.asyncio
async def test_read_file_returns_the_file_content() -> None:
    sandbox = FakeSandbox()
    sandbox.files.store["/home/user/probe.py"] = "print(1)\n"
    server = build_sandbox_server(sandbox)

    out = await _call(server, "read_file", {"path": "/home/user/probe.py"})

    assert "__error__" not in out
    assert out == {"path": "/home/user/probe.py", "content": "print(1)\n"}
    assert sandbox.files.reads == ["/home/user/probe.py"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("run_command", {"command": ""}),
        ("write_file", {"path": "", "content": "x"}),
        ("write_file", {"path": "/w/x.py"}),
        ("read_file", {}),
    ],
)
async def test_missing_required_args_are_an_error_and_never_touch_the_sandbox(
    tool_name: str, args: dict[str, Any]
) -> None:
    sandbox = FakeSandbox()
    server = build_sandbox_server(sandbox)

    out = await _call(server, tool_name, args)

    assert "__error__" in out
    assert sandbox.commands.calls == []
    assert sandbox.files.writes == []
    assert sandbox.files.reads == []


# ── AC3: never-throw — a sandbox fault is an is_error result, not an exception ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("run_command", {"command": "pytest -q"}),
        ("write_file", {"path": "/w/x.py", "content": "print(1)"}),
        ("read_file", {"path": "/w/x.py"}),
    ],
)
async def test_sandbox_fault_returns_is_error_never_raises(tool_name: str, args: dict[str, Any]) -> None:
    server = build_sandbox_server(RaisingSandbox())

    out = await _call(server, tool_name, args)  # must not raise

    assert out.get("__error__") is not None
    assert "e2b 502" in out["__error__"]


# ── AC4: SANDBOX_TOOLS exact fully-qualified names ────────────────────────────


def test_sandbox_tools_names_the_three_canonical_sandbox_tools() -> None:
    assert SANDBOX_TOOLS == (
        "mcp__sandbox__run_command",
        "mcp__sandbox__write_file",
        "mcp__sandbox__read_file",
    )
