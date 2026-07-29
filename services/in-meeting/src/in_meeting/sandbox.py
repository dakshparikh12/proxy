"""Sandbox access — the warm per-meeting E2B sandbox + the ``sandbox`` toolbelt (SANDBOX).

The last piece of Proxy's access surface: the ability to EXECUTE code in a real,
isolated sandbox. :func:`provision_sandbox` spins exactly ONE E2B sandbox for the
meeting at join (kept warm for the meeting's lifetime — provisioned with a
meeting-length ``timeout`` because e2b's own default is only 300s; the caller owns
teardown via the handle's ``kill()``); :func:`build_sandbox_server` mounts that handle as
COMPOSABLE MCP tools — run a command, write a file, read a file — under the
``sandbox`` server name, the exact ``create_sdk_mcp_server`` recipe
``meeting_control.build_meeting_control_server`` and premeeting's
``build_repo_context_server`` use. The ENGINE never decides to run anything
(Law 4 — no situation→action mapping lives in code); the AGENT composes these
three primitives for heavy/code work; this module owns only the pipe.

Seam compliance (§14 hard rule — every external call wrapped): the real E2B
round-trips ride the ONE ``call_external`` seam in ``libs/http``; the raw
``AsyncSandbox`` class is referenced ONLY via ``libs/http.e2b_sandbox_class()``
(the sole raw-client home — this module never imports ``e2b``, and the
``check-call-external`` guard enforces it). The E2B key rides the settings/env
surface (``E2B_API_KEY``, resolved by the SDK inside the seam) — never passed,
hard-coded, or logged here.

Isolation + hardening: one sandbox is provisioned PER MEETING with a CURATED env
(exactly what the caller passes — never the host process environment), and
egress is default-DENY: the confirmed e2b create kwarg
``allow_internet_access=False`` is threaded on every provision unless the caller
explicitly opens it. (The finer-grained ``network=`` allow-list kwarg exists on
the installed SDK and is deliberately not threaded until a caller needs a
curated allow-list.)

Every handler is NEVER-THROW (Hard Rule 6): a sandbox fault returns an
``is_error`` result the agent can hear about and speak plainly (Law 2), never a
raised exception. A NON-ZERO exit code is a RESULT, not a fault — the installed
e2b SDK reports it as a ``CommandExitException`` carrying stdout/stderr/exit_code,
and ``run_command`` hands that back to the agent as data.

Callers mount it the CODE-LOOKUP way: ``allowed_tools = CODE_TOOLS +
MEETING_TOOLS + SANDBOX_TOOLS`` with ``mcp_servers={..., "sandbox":
build_sandbox_server(sandbox)}`` — the Engine just threads what it's given.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

# The server name the fully-qualified ``mcp__sandbox__*`` allowed_tools resolve against.
SERVER_NAME = "sandbox"

#: The sandbox lifetime threaded to the confirmed e2b ``create(timeout=...)`` kwarg
#: (seconds). e2b's OWN ``default_sandbox_timeout`` is only 300s — five minutes into
#: a real 30-60 min meeting the sandbox would silently die, so provisioning threads
#: a meeting-length hour by default. A meeting LONGER than this must be kept alive
#: by the runtime periodically extending the handle (``set_timeout``) — a keep-warm
#: heartbeat that is a documented runtime follow-up, not built here.
SANDBOX_TIMEOUT_S: int = 3600

#: The per-command timeout threaded to the confirmed e2b ``commands.run(timeout=...)``
#: kwarg (seconds). e2b's own per-command default is 60s — too short for the
#: heavy/code work this toolbelt exists for (test suites, builds).
COMMAND_TIMEOUT_S: int = 300

# The sandbox-access tool basenames, in the order the server advertises them.
TOOL_BASENAMES: tuple[str, ...] = ("run_command", "write_file", "read_file")

#: The fully-qualified tool names callers pass as ``allowed_tools`` (the
#: ``CODE_TOOLS``/``MEETING_TOOLS`` pattern): ``mcp__<SERVER_NAME>__<basename>``.
SANDBOX_TOOLS: tuple[str, ...] = (
    "mcp__sandbox__run_command",
    "mcp__sandbox__write_file",
    "mcp__sandbox__read_file",
)

#: The injectable provision backend: an async factory with the e2b
#: ``AsyncSandbox.create`` keyword surface (``envs=``, ``metadata=``,
#: ``allow_internet_access=``, ``timeout=``) returning the live sandbox handle.
#: Tests inject a recording fake; the default is the real create THROUGH the
#: call_external seam.
SandboxBackend = Callable[..., Awaitable[Any]]


class SandboxHandle(Protocol):
    """The e2b ``AsyncSandbox`` surface this toolbelt touches, stated structurally
    so a fake mounts in tests exactly like the live handle (confirmed installed
    surface: ``commands.run(cmd)``, ``files.write(path, data)`` /
    ``files.read(path)``; the handle also carries ``kill()`` for the caller's
    teardown, which this toolbelt never invokes)."""

    @property
    def commands(self) -> Any: ...

    @property
    def files(self) -> Any: ...


def _text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _error_result(msg: str) -> dict[str, Any]:
    """The never-throw boundary (Hard Rule 6): a tool fault returns an ``is_error`` result."""
    return {"is_error": True, "content": [{"type": "text", "text": msg}]}


async def _via_seam(op: Callable[[], Awaitable[Any]], *, max_retries: int = 3) -> Any:
    """Issue one sandbox round-trip through the ONE external-call seam (retry + cost
    telemetry, §14). Imported lazily so mounting the toolbelt against an in-process
    fake never drags the seam's transitive deps into the hot import path."""
    from libs.http.src.http import external as _http

    outcome = await _http.call_external(op, service="e2b", max_retries=max_retries)
    return outcome.value


async def _real_create(**create_kwargs: Any) -> Any:
    """The default provision backend: the REAL e2b ``AsyncSandbox.create`` THROUGH the seam.

    The raw class is resolved ONLY via ``libs/http.e2b_sandbox_class()`` (the sole
    raw-client home; ``ImportError`` iff e2b is absent — an honest degrade the caller
    decides on). The E2B key is resolved by the SDK from ``E2B_API_KEY`` (the
    settings/env surface) inside the seam — never threaded or logged here."""
    from libs.http.src.http import external as _http

    cls = _http.e2b_sandbox_class()
    outcome = await _http.call_external(lambda: cls.create(**create_kwargs), service="e2b")
    return outcome.value


async def provision_sandbox(
    *,
    backend: SandboxBackend | None = None,
    env: Mapping[str, str] | None = None,
    metadata: Mapping[str, str] | None = None,
    allow_internet_access: bool = False,
    timeout_s: int = SANDBOX_TIMEOUT_S,
) -> Any:
    """Provision exactly ONE warm E2B sandbox for the meeting; returns the live handle.

    ``backend`` is injectable (tests pass a recording fake; the default is the real
    ``AsyncSandbox.create`` through the ``call_external`` seam). The env is CURATED:
    the backend sees exactly the entries the caller passed — never the host process
    environment. Egress is default-DENY (``allow_internet_access=False`` threaded to
    the confirmed e2b create kwarg); a caller that needs the network opens it
    explicitly.

    The sandbox is provisioned with ``timeout_s`` (default 1 hour — a meeting-length
    lifetime) threaded to the confirmed e2b ``create(timeout=)`` kwarg, because e2b's
    own default is only 300s — five minutes into a real meeting the sandbox would
    silently die. A meeting LONGER than the timeout must be kept alive by the runtime
    periodically extending the handle (``set_timeout``) — a keep-warm heartbeat that
    is a documented follow-up for the runtime, NOT built here. The caller still owns
    teardown via the handle's ``kill()`` at meeting end.
    """
    create = backend if backend is not None else _real_create
    return await create(
        envs=dict(env or {}),
        metadata=dict(metadata or {}),
        allow_internet_access=allow_internet_access,
        timeout=timeout_s,
    )


def build_sandbox_server(sandbox: SandboxHandle) -> McpSdkServerConfig:
    """Build the in-process sandbox-access SDK server over ONE meeting's sandbox handle.

    The handle is bound at build time — a server is built PER MEETING for THAT
    meeting's sandbox, so a tool call can never execute in another meeting's sandbox.
    Every handler drives the REAL sandbox verb through the ``call_external`` seam and
    NEVER throws: a fault comes back as an ``is_error`` result (Hard Rule 6); a
    non-zero exit comes back as the command's honest result (Law 2)."""

    @tool(
        "run_command",
        "Run a shell command in the meeting's sandbox; returns stdout, stderr, and exit_code.",
        {"command": str},
    )
    async def run_command(args: dict[str, Any]) -> dict[str, Any]:
        try:
            command = str(args.get("command") or "")
            if not command.strip():
                return _error_result("run_command error: command is required")
            # max_retries=1 — a command run is NOT idempotent; the seam must never
            # re-execute it on a transport blip (retry stays for the idempotent verbs).
            # timeout=COMMAND_TIMEOUT_S — e2b's per-command default is 60s, too short
            # for the heavy/code work this tool exists for.
            result = await _via_seam(
                lambda: sandbox.commands.run(command, timeout=COMMAND_TIMEOUT_S),
                max_retries=1,
            )
            return _text_result(
                {
                    "stdout": str(getattr(result, "stdout", "") or ""),
                    "stderr": str(getattr(result, "stderr", "") or ""),
                    "exit_code": int(getattr(result, "exit_code", 0) or 0),
                }
            )
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            exit_code = getattr(exc, "exit_code", None)
            if exit_code is not None:
                # The installed e2b SDK raises CommandExitException — an exception
                # CARRYING the CommandResult — on a non-zero exit. A failing command
                # is a RESULT the agent composes with, not a tool fault.
                return _text_result(
                    {
                        "stdout": str(getattr(exc, "stdout", "") or ""),
                        "stderr": str(getattr(exc, "stderr", "") or ""),
                        "exit_code": int(exit_code),
                    }
                )
            return _error_result(f"run_command error: {exc}")

    @tool(
        "write_file",
        "Write a file into the meeting's sandbox at the given path (creates or overwrites).",
        {"path": str, "content": str},
    )
    async def write_file(args: dict[str, Any]) -> dict[str, Any]:
        try:
            path = str(args.get("path") or "")
            if not path.strip():
                return _error_result("write_file error: path is required")
            content = args.get("content")
            if not isinstance(content, str):
                return _error_result("write_file error: content (a string) is required")
            await _via_seam(lambda: sandbox.files.write(path, content))
            return _text_result({"written": True, "path": path})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"write_file error: {exc}")

    @tool(
        "read_file",
        "Read a file from the meeting's sandbox at the given path (returns its content).",
        {"path": str},
    )
    async def read_file(args: dict[str, Any]) -> dict[str, Any]:
        try:
            path = str(args.get("path") or "")
            if not path.strip():
                return _error_result("read_file error: path is required")
            text = await _via_seam(lambda: sandbox.files.read(path))
            content = text if isinstance(text, str) else str(text)
            return _text_result({"path": path, "content": content})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"read_file error: {exc}")

    handlers = {"run_command": run_command, "write_file": write_file, "read_file": read_file}
    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0", tools=[handlers[n] for n in TOOL_BASENAMES]
    )


__all__ = [
    "COMMAND_TIMEOUT_S",
    "SANDBOX_TIMEOUT_S",
    "SANDBOX_TOOLS",
    "SERVER_NAME",
    "TOOL_BASENAMES",
    "SandboxBackend",
    "SandboxHandle",
    "build_sandbox_server",
    "provision_sandbox",
]
