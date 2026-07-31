"""The WORKROOM — native Claude Code running INSIDE a per-meeting E2B sandbox.

This is the new in-meeting brain (it replaces the custom MCP-tool engine). The sandbox
IS the workspace: the repo is cloned into it, the live meeting transcript is written into
it as a file, and native ``claude`` runs in it with its full built-in tools (Read/Edit/
Bash/Grep/Glob/WebSearch/sub-agents) on the real code + scratch space. Any reactive task —
code, a doc, a UI mockup, research, a draft PR — is planned and DONE for real in here, then
the result is handed back to the bridge to present to the room.

Auth is the founder's SUBSCRIPTION carried into the sandbox as ``CLAUDE_CODE_OAUTH_TOKEN``
(proven: native ``claude -p`` authenticates on it, ~$0). World-touching is impossible from
inside by construction — the sandbox holds no push/send credentials and egress is denied —
so an irreversible action becomes a staged DRAFT the trusted host applies behind a human
click (Law 3).

Every E2B round-trip rides the single ``libs/http.call_external`` seam (retry + cost
telemetry; the hard rule — no raw client outside ``libs/http``).
"""
from __future__ import annotations

import json
import logging
import pathlib
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# The richer PRIME lives in ``in_meeting.prime`` (it describes the ONE ``to_meeting`` tool +
# the dynamic mediums). Production seeds THAT prime as CLAUDE.md so native Claude knows it can
# choose speak/chat/dm/screen/offer/mute live — there is no second, weaker prime here.
from .prime import WORKROOM_PRIME

logger = logging.getLogger(__name__)

#: Where the repo + seed files live inside the sandbox (the user's home; root paths are
#: not writable by the default sandbox user — verified).
WORKROOM_ROOT = "/home/user/work"
REPO_DIR = f"{WORKROOM_ROOT}/repo"
TRANSCRIPT_FILE = f"{REPO_DIR}/MEETING_NOTES.md"
MAP_FILE = f"{REPO_DIR}/REPO_MAP.md"
PRIME_FILE = f"{REPO_DIR}/CLAUDE.md"

#: The in-sandbox MCP server that gives native Claude its ONE connection to the room (the
#: ``to_meeting`` tool). Written into the sandbox at provision from the packaged source; native
#: ``claude`` loads it via ``.mcp.json`` (stdio). It relays each ``to_meeting`` call to the host.
MCP_SERVER_FILE = f"{REPO_DIR}/sandbox_meeting_mcp.py"
MCP_CONFIG_FILE = f"{REPO_DIR}/.mcp.json"
#: Where the in-sandbox MCP server appends the agent's ``to_meeting`` intents when NOT relaying
#: live (proof/simulation) — kept as a stable local record even in live mode (on a relay fault).
TO_MEETING_OUT = "/tmp/to_meeting.jsonl"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
#: The pinned MCP SDK the in-sandbox server needs (matches the proof battery + the bake target).
MCP_PIN = "mcp==1.28.1"

#: Timeouts (seconds). The sandbox itself outlives any single ask (keep-warm heartbeat
#: bumps it); a single ask is bounded so a runaway turn can't stall the meeting.
PROVISION_TIMEOUT_S = 1800.0
ASK_TIMEOUT_S = 900.0

#: The default pre-baked E2B template (repo + node + claude + deps warm). Until it is baked,
#: ``None`` provisions a base sandbox and sets it up at warm time (proven path).
DEFAULT_TEMPLATE: str | None = None

# The E2B command/file round-trips ride this seam signature: a thunk returning an awaitable,
# wrapped by ``call_external`` (retry + telemetry). Injected so tests pass a fake.
Seam = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class WorkroomResult:
    """One reactive task's outcome, as the bridge will present it to the room."""

    ask: str
    text: str = ""                       # the plain result meant to be spoken/posted
    tools: list[str] = field(default_factory=list)   # ordered native tool names used
    turns: int = 0                       # sdk turns / duration proxy
    cost_usd: float = 0.0
    error: str | None = None             # honest fault, never re-thrown into the loop


@dataclass(slots=True)
class Workroom:
    """A live per-meeting workroom: a native-Claude E2B sandbox with the repo + transcript.

    Constructed by :func:`provision_workroom`; driven by the bridge — ``feed_transcript`` on
    every line, ``run_ask`` on every wake, ``teardown`` at meeting end.
    """

    sandbox: Any                         # the live AsyncSandbox handle
    call: Seam                           # the call_external seam (bound)
    token: str                           # CLAUDE_CODE_OAUTH_TOKEN (subscription)
    repo_dir: str = REPO_DIR
    #: The host relay endpoint the in-sandbox MCP server POSTs each ``to_meeting`` call to
    #: (``<PUBLIC_BASE_URL>/meetings/<id>/relay``). Empty ⇒ no relay: the agent's dynamic
    #: mediums are recorded locally only, and the session honest-degrades to result-text speak.
    relay_url: str = ""
    #: The per-meeting bearer the relay route authenticates. Empty when there is no relay.
    relay_token: str = ""

    @property
    def sandbox_id(self) -> str:
        return str(getattr(self.sandbox, "sandbox_id", "") or "")

    async def _run(self, cmd: str, *, timeout: float, envs: dict[str, str] | None = None) -> Any:
        """One command in the sandbox, through the seam."""
        outcome = await self.call(
            lambda: self.sandbox.commands.run(cmd, timeout=int(timeout), envs=envs or {}),
            service="e2b",
        )
        return getattr(outcome, "value", outcome)

    async def _write_file(self, path: str, content: str) -> None:
        outcome = await self.call(lambda: self.sandbox.files.write(path, content), service="e2b")
        getattr(outcome, "value", outcome)

    async def feed_transcript(self, transcript_md: str) -> None:
        """Materialize the live transcript into the sandbox (the workroom always holds the
        latest meeting notes). The bridge calls this as lines accumulate — a full rewrite is
        simplest + cheap; the file is what a woken turn reads."""
        try:
            await self._write_file(TRANSCRIPT_FILE, transcript_md)
        except Exception:  # noqa: BLE001 — transcript sync never crashes the meeting
            logger.exception("workroom transcript sync failed (meeting continues)")

    async def run_ask(self, ask: str) -> WorkroomResult:
        """Wake native Claude in the workroom on ONE reactive ask; return the result.

        Native ``claude`` runs WITH the in-sandbox meeting MCP server (``--mcp-config``): the
        agent reaches the room DURING the turn through its ONE ``to_meeting`` tool, choosing the
        medium (speak/chat/dm/screen/offer/mute) live — the host relays each call to the real
        meeting connection. The relay envs (``PROXY_MEETING_RELAY``/``PROXY_MEETING_TOKEN``/
        ``PROXY_MEETING_OUT``) point the MCP server at the host; when there is no relay it records
        the intents locally and the session honest-degrades to speaking the result text.

        Never raises — a failed turn is an honest ``WorkroomResult.error`` (§9)."""
        prompt = (
            "The room just addressed you. Your identity and how to behave are in ./CLAUDE.md; "
            "the live meeting transcript is in ./MEETING_NOTES.md.\n\n"
            f"The latest ask addressed to you:\n{ask}\n\n"
            "Do it for real now, then reach the room through your `to_meeting` tool — you choose "
            "what to convey and how (say/chat/dm/screen/offer/mute), live, like a great teammate."
        )
        cmd = (
            f"cd {shlex.quote(self.repo_dir)} && "
            f"claude -p {shlex.quote(prompt)} "
            f"--mcp-config {shlex.quote(MCP_CONFIG_FILE)} --dangerously-skip-permissions "
            f"--output-format stream-json --verbose > /tmp/ask.jsonl 2>/tmp/ask.err; echo DONE"
        )
        # The subscription auth + the relay wiring for the in-sandbox MCP server. When
        # ``relay_url`` is empty the server falls back to appending intents to ``PROXY_MEETING_OUT``
        # (no live relay) — an honest degrade, never a crash.
        envs = {
            "CLAUDE_CODE_OAUTH_TOKEN": self.token,
            "PROXY_MEETING_RELAY": self.relay_url,
            "PROXY_MEETING_TOKEN": self.relay_token,
            "PROXY_MEETING_OUT": TO_MEETING_OUT,
        }
        try:
            await self._run(cmd, timeout=ASK_TIMEOUT_S, envs=envs)
            raw = getattr(await self.call(lambda: self.sandbox.files.read("/tmp/ask.jsonl"),  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
                                          service="e2b"), "value", "")
            return _parse_stream(ask, raw or "")
        except Exception as exc:  # noqa: BLE001 — never crash the loop
            logger.exception("workroom run_ask failed")
            return WorkroomResult(ask=ask, error=str(exc) or exc.__class__.__name__)

    async def teardown(self) -> None:
        """Kill the sandbox (meeting end / cleanup)."""
        try:
            await self.call(lambda: self.sandbox.kill(), service="e2b")
        except Exception:  # noqa: BLE001
            logger.exception("workroom teardown kill failed")


def _parse_stream(ask: str, raw: str) -> WorkroomResult:
    """Parse ``claude`` stream-json output → the ordered tool names + the final result text."""
    tools: list[str] = []
    text = ""
    cost = 0.0
    turns = 0
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            turns += 1
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tools.append(str(b.get("name", "")))
        elif ev.get("type") == "result":
            text = str(ev.get("result", "") or "")
            cost = float(ev.get("total_cost_usd", 0.0) or 0.0)
    return WorkroomResult(ask=ask, text=text, tools=tools, turns=turns, cost_usd=cost)


def _mcp_server_source() -> str:
    """The packaged in-sandbox MCP server source, read from the ``in_meeting`` package.

    The script runs INSIDE the sandbox (where the workspace is not installed), so it is copied
    in verbatim at provision. Read it off disk next to this module — the ONE source of truth,
    identical to the file the proof battery seeds — rather than duplicating its text here."""
    return (pathlib.Path(__file__).with_name("sandbox_meeting_mcp.py")).read_text(encoding="utf-8")


async def provision_workroom(
    *,
    call: Seam,
    token: str,
    repo_url: str,
    sha: str | None = None,
    map_text: str = "",
    prime: str = WORKROOM_PRIME,
    template: str | None = DEFAULT_TEMPLATE,
    sandbox_class: Any = None,
    relay_url: str = "",
    relay_token: str = "",
) -> Workroom:
    """Provision + seed a per-meeting workroom, warm and ready before the first ask.

    Provisions an E2B sandbox (pre-baked ``template`` when available), clones the repo in
    (shallow), ensures native ``claude`` is present, and seeds the prime (CLAUDE.md), the map
    (REPO_MAP.md), and an empty transcript file. It ALSO wires the agent's ONE meeting interface:
    pip-installs the pinned ``mcp`` SDK, writes the ``sandbox_meeting_mcp.py`` server in, and drops
    a ``.mcp.json`` pointing native ``claude`` at it (stdio) — so a woken turn can call
    ``to_meeting`` and reach the room live. ``relay_url`` / ``relay_token`` are stashed on the
    :class:`Workroom` for :meth:`Workroom.run_ask` to hand the MCP server as relay envs; empty ⇒
    no relay (intents recorded locally; the session speaks the result text). Egress stays
    default-deny; the ONLY credential injected is the subscription ``CLAUDE_CODE_OAUTH_TOKEN`` (no
    push/send creds — the Law-3 gate by construction). All E2B round-trips ride ``call`` (the
    call_external seam).
    """
    if sandbox_class is None:
        # The full path (not ``http.external``) so our ``http`` package never shadows the
        # stdlib ``http`` — the codebase-wide convention for the call_external seam.
        from libs.http.src.http.external import e2b_sandbox_class

        sandbox_class = e2b_sandbox_class()

    create_kwargs: dict[str, Any] = {"timeout": int(PROVISION_TIMEOUT_S)}
    if template:
        create_kwargs["template"] = template
    outcome = await call(lambda: sandbox_class.create(**create_kwargs), service="e2b",
                         unit_cost_usd=0.0)
    sandbox = getattr(outcome, "value", outcome)
    wr = Workroom(sandbox=sandbox, call=call, token=token,
                  relay_url=relay_url, relay_token=relay_token)

    # Setup (idempotent; a pre-baked template makes most of this a no-op/instant):
    #  - shallow-clone the repo into REPO_DIR (fast; ~6s on cal.com)
    #  - ensure native claude is installed
    #  - install the pinned mcp SDK the in-sandbox meeting server needs
    depth = "--depth 1" if sha is None else ""
    setup = (
        f"mkdir -p {shlex.quote(WORKROOM_ROOT)} && "
        f"(test -d {shlex.quote(REPO_DIR)}/.git || git clone {depth} {shlex.quote(repo_url)} "
        f"{shlex.quote(REPO_DIR)}) && "
        f"(command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code) && "
        f"pip3 install -q {shlex.quote(MCP_PIN)}"
    )
    await wr._run(setup, timeout=PROVISION_TIMEOUT_S)  # noqa: SLF001 — same module
    if sha:
        await wr._run(f"cd {shlex.quote(REPO_DIR)} && git checkout -q {shlex.quote(sha)} || true",
                      timeout=120.0)  # noqa: SLF001
    # Seed the orientation files.
    await wr._write_file(PRIME_FILE, prime)  # noqa: SLF001
    await wr._write_file(MAP_FILE, map_text or "(no pre-built map — explore the repo directly)")  # noqa: SLF001
    await wr._write_file(TRANSCRIPT_FILE, "# Meeting transcript\n")  # noqa: SLF001
    # Wire the agent's ONE connection to the room: the in-sandbox MCP server + the .mcp.json that
    # registers it with native ``claude`` over stdio. The agent chooses the medium live; the server
    # relays each call to the host (or records it locally when there is no relay).
    await wr._write_file(MCP_SERVER_FILE, _mcp_server_source())  # noqa: SLF001
    mcp_config = {
        "mcpServers": {
            "meeting": {"command": "python3", "args": [MCP_SERVER_FILE]}
        }
    }
    await wr._write_file(MCP_CONFIG_FILE, json.dumps(mcp_config))  # noqa: SLF001
    logger.info("workroom provisioned: sandbox=%s repo=%s relay=%s", wr.sandbox_id, repo_url,
                bool(relay_url))
    return wr
