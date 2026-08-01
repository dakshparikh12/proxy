"""The WARM SESSION HOST — ONE persistent native-Claude session per meeting, INSIDE the sandbox.

This standalone script runs in the workroom sandbox (copied in at provision, started as a
background process before the first wake). It replaces the cold ``claude -p`` spawn per wake:
that path re-spawned the ``to_meeting`` MCP server, re-discovered the tool, and reloaded the
prime EVERY turn (~11-13s). Here ONE :class:`claude_agent_sdk.ClaudeSDKClient` is opened once —
the prime (``CLAUDE.md``) and the meeting MCP server (``to_meeting``) load ONCE — and each wake is
just a ``client.query(prompt)`` on the already-warm session (~1-3s; spike-confirmed ~1.2s).

It imports nothing from the workspace (it runs where the workspace is not installed); its only
deps are ``claude-agent-sdk`` + ``mcp``, pip-installed into the sandbox at provision.

The protocol (files, so the host driver stays dependency-light and never shares a process):

* IN  — ``$PROXY_WAKE_IN`` (default ``/tmp/wake_in.jsonl``): the driver APPENDS one JSON object
  ``{"id": "<uuid>", "prompt": "<the wake prompt>"}`` per wake. The host tails the file.
* OUT — ``$PROXY_WAKE_OUT`` (default ``/tmp/wake_out``): the host writes ``<id>.json`` with the
  SAME shape the cold path's ``_parse_stream`` produced: ``{tools, text, cost_usd, turns,
  error, sent}``. Written atomically (temp + rename) so the poller never reads a half-file.
* The per-turn ``to_meeting`` intents are read from ``$PROXY_MEETING_OUT`` (the same file the
  in-sandbox MCP server records to), which the host TRUNCATES before each turn so a wake's
  ``sent`` is exactly that turn's intents.

Never throws: a per-turn failure is written as ``{"error": ...}`` so the driver degrades
honestly (§9). A fatal startup failure writes a ``$PROXY_WAKE_OUT/_host.err`` breadcrumb and
exits, so the driver's poll times out and falls back to the cold path.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from typing import Any

#: The persistent session's default model — a FAST (Haiku) id so a quick ask is sub-second.
#: Overridable via ``PROXY_WORKROOM_MODEL`` (Law 4: no per-task model in code; the agent still
#: escalates heavy work to a stronger model through its own sub-agents).
DEFAULT_MODEL = "claude-haiku-4-5"

WAKE_IN = os.environ.get("PROXY_WAKE_IN", "/tmp/wake_in.jsonl")  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM
WAKE_OUT = os.environ.get("PROXY_WAKE_OUT", "/tmp/wake_out")  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM
MEETING_OUT = os.environ.get("PROXY_MEETING_OUT", "/tmp/to_meeting.jsonl")  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM
REPO_DIR = os.environ.get("PROXY_REPO_DIR", "/home/user/work/repo")
MCP_SERVER_FILE = os.environ.get("PROXY_MCP_SERVER", f"{REPO_DIR}/sandbox_meeting_mcp.py")
MODEL = (os.environ.get("PROXY_WORKROOM_MODEL", "") or DEFAULT_MODEL).strip()

#: How long the host waits for the next wake line before re-polling (seconds). The session stays
#: warm across this idle; the driver's append is picked up within one beat.
_POLL_S = 0.1


def _parse_intents(raw: str) -> list[dict[str, Any]]:
    """The in-sandbox MCP server's recorded ``to_meeting`` intents → ``[{content, medium, to}]`` —
    the agent's OWN channel choices this turn. Skips relay-error/malformed lines (best-effort record).
    Mirrors ``workroom._parse_intents`` byte-for-byte so warm + cold produce identical ``sent``."""
    intents: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or "relay_error" in rec:
            continue
        intents.append({
            "content": str(rec.get("content", "") or ""),
            "medium": str(rec.get("medium", "say") or "say"),
            "to": str(rec.get("to", "") or ""),
        })
    return intents


def _read_intents() -> str:
    try:
        return pathlib.Path(MEETING_OUT).read_text(encoding="utf-8")
    except OSError:
        # A MISSING file is the NORMAL silence case — the agent chose not to call to_meeting.
        return ""


def _reset_intents() -> None:
    """Truncate the intents file so a turn's ``sent`` is EXACTLY this turn's ``to_meeting`` calls
    (the file is append-only within a turn; the host owns the per-turn boundary)."""
    try:
        os.remove(MEETING_OUT)
    except OSError:
        pass


def _write_result(wake_id: str, record: dict[str, Any]) -> None:
    """Write the wake result atomically (temp + rename) so the driver's poll never sees a partial."""
    out_dir = pathlib.Path(WAKE_OUT)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{wake_id}.json"
    tmp = out_dir / f".{wake_id}.json.tmp"
    tmp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(tmp, final)


async def _run_turn(client: Any, prompt: str) -> dict[str, Any]:
    """One wake on the WARM session: query, drain the response, capture the SAME data the cold
    ``_parse_stream`` produced (ordered tool names, final text, cost, turns) + the recorded intents.
    Never raises — a per-turn error is returned as ``{"error": ...}`` (the driver degrades honestly)."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

    tools: list[str] = []
    text = ""
    last_text = ""
    cost = 0.0
    turns = 0
    saw_result = False
    _reset_intents()
    try:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                turns += 1
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tools.append(str(block.name or ""))
                    elif isinstance(block, TextBlock) and str(block.text or "").strip():
                        last_text = str(block.text).strip()
            elif isinstance(msg, ResultMessage):
                saw_result = True
                text = str(msg.result or "")
                cost = float(msg.total_cost_usd or 0.0)
                if msg.num_turns:
                    turns = int(msg.num_turns)
    except Exception as exc:  # noqa: BLE001 — a per-turn fault is an honest error, never a crash
        return {"tools": tools, "text": last_text, "cost_usd": cost, "turns": turns,
                "error": str(exc) or exc.__class__.__name__,
                "sent": _parse_intents(_read_intents())}

    intents = _parse_intents(_read_intents())
    # Salvage an abnormally-terminated turn's last prose (parity with ``_parse_stream``).
    if not saw_result and not text:
        text = last_text
    error = None
    if turns > 0 and not saw_result and not intents:
        error = "turn did not complete"
    return {"tools": tools, "text": text, "cost_usd": cost, "turns": turns,
            "error": error, "sent": intents}


async def _serve(client: Any) -> None:
    """Tail ``$PROXY_WAKE_IN`` and serve each wake on the warm session, forever (until killed)."""
    in_path = pathlib.Path(WAKE_IN)
    in_path.parent.mkdir(parents=True, exist_ok=True)
    in_path.touch(exist_ok=True)
    seen = 0  # lines already consumed (append-only file; the driver only ever appends)
    while True:
        try:
            lines = in_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        while seen < len(lines):
            raw = lines[seen]
            seen += 1
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
                wake_id = str(req["id"])
                prompt = str(req["prompt"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # a malformed line is skipped (the driver's poll will time out → cold path)
            record = await _run_turn(client, prompt)
            record["_served_at"] = time.time()
            _write_result(wake_id, record)
        await asyncio.sleep(_POLL_S)


def _mcp_servers() -> dict[str, Any]:
    """The meeting MCP server config for the WARM session — the SAME stdio server the cold path
    registered via ``.mcp.json``, so ``to_meeting`` loads ONCE for the whole session. The relay envs
    (``PROXY_MEETING_RELAY``/``_TOKEN``/``_OUT``) are already in this process's env and inherited by
    the stdio child, so a live turn's ``to_meeting`` reaches the host exactly as before."""
    return {
        "meeting": {"type": "stdio", "command": "python3", "args": [MCP_SERVER_FILE]}
    }


async def main() -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    options = ClaudeAgentOptions(
        cwd=REPO_DIR,
        permission_mode="bypassPermissions",   # the sandbox holds no push/send creds (Law 3)
        setting_sources=["project"],           # loads CLAUDE.md (the prime) ONCE
        model=MODEL,                            # fast default; heavy work escalates via sub-agents
        mcp_servers=_mcp_servers(),
    )
    async with ClaudeSDKClient(options=options) as client:
        # Mark the host READY so the driver can tell the warm session came up (vs. a startup fault).
        try:
            pathlib.Path(WAKE_OUT).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(WAKE_OUT) / "_host.ready").write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass
        await _serve(client)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 — a fatal startup fault leaves a breadcrumb, never silent
        try:
            pathlib.Path(WAKE_OUT).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(WAKE_OUT) / "_host.err").write_text(
                f"{type(exc).__name__}: {exc}", encoding="utf-8")
        except OSError:
            pass
        raise
