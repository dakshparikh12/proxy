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
import re
import time
import urllib.request
from typing import Any

#: The persistent session's default model. Sonnet (not Haiku): the real-data dry-run proved Haiku
#: OVER-EXPLORES a normal ask and exhausts the turn budget WITHOUT calling ``to_meeting`` — it wakes
#: but delivers nothing (a fast model that says nothing is useless). Sonnet follows the "deliver in
#: one turn" instruction reliably. Overridable via ``PROXY_WORKROOM_MODEL`` (Law 4: no per-task model
#: in code; the agent still escalates heavy work to a stronger model through its own sub-agents).
DEFAULT_MODEL = "claude-sonnet-4-6"

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


#: Sentence terminators that close a spoken chunk. Comma/colon are intentionally NOT here — flushing
#: on those makes the voice choppy (industry consensus: Pipecat/LiveKit flush on sentence ends only).
_TERMINATORS = ".!?;…"
#: Common abbreviations whose trailing dot must NOT end a spoken sentence.
_ABBREVS = ("mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "inc.", "ltd.", "jr.",
            "sr.", "no.", "fig.", "approx.")
_WS = re.compile(r"\s")


def _sentence_end(buf: str) -> int | None:
    """Index just past the first COMPLETE sentence in ``buf``, or ``None`` if none has closed yet.

    A sentence closes at ``. ! ? ; …`` that is (a) FOLLOWED BY whitespace — so mid-token dots in
    ``core.py`` / ``main()`` and the still-growing tail of the buffer don't trigger — (b) NOT
    preceded by a digit (so ``3.14`` stays whole), and (c) not the tail of a common abbreviation
    (``e.g.``). This keeps each flushed chunk a natural spoken clause. The final partial sentence
    (no trailing whitespace yet) is force-flushed by the caller at stream end."""
    for i, ch in enumerate(buf):
        if ch not in _TERMINATORS:
            continue
        nxt = buf[i + 1] if i + 1 < len(buf) else ""
        if not (nxt and _WS.match(nxt)):          # needs a following whitespace char to be "closed"
            continue
        prev = buf[i - 1] if i > 0 else ""
        if prev.isdigit():                          # 3.14 — not a sentence end
            continue
        head = buf[: i + 1].rstrip().lower()
        if any(head.endswith(a) for a in _ABBREVS):  # e.g. / i.e. / Dr. — not a sentence end
            continue
        return i + 1
    return None


def _relay_post(url: str, rec: dict[str, Any]) -> None:
    """POST one delivery to the host relay — same shape/auth as the in-sandbox MCP's ``_relay``."""
    data = json.dumps(rec).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    token = os.environ.get("PROXY_MEETING_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310  # nosec B310 — fixed host relay URL
        resp.read()


async def _deliver_say(content: str) -> None:
    """Stream ONE spoken sentence to the room the instant it closes — the voice channel itself.

    LIVE (``PROXY_MEETING_RELAY`` set): POST it to the host relay right now, mid-turn, so the room
    hears sentence 1 while the model is still writing sentence 3. The blocking ``urllib`` POST runs
    off the event loop (``to_thread``) but is AWAITED, so sentences reach the room strictly in order
    without stalling the token stream. PROOF/file mode: append it to the intents file (medium
    ``say``) so the driver's ``sent`` reflects exactly what was spoken, in order. Never raises — a
    send fault degrades to a recorded ``relay_error`` line, never a crash."""
    rec: dict[str, Any] = {"ts": time.time(), "content": content, "medium": "say", "to": ""}
    relay = os.environ.get("PROXY_MEETING_RELAY", "").strip()
    if relay:
        try:
            await asyncio.to_thread(_relay_post, relay, rec)
            return
        except Exception as exc:  # noqa: BLE001 — never crash the agent's turn on a send fault
            rec = {**rec, "relay_error": str(exc)}
    try:
        with open(MEETING_OUT, "a", encoding="utf-8") as f:  # noqa: PTH123 — tiny append, in-sandbox
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


async def _run_turn(client: Any, prompt: str) -> dict[str, Any]:
    """One wake on the WARM session: query and drain the response, STREAMING the agent's spoken
    prose to the room sentence-by-sentence as it is generated (the voice channel — first audio at
    the first clause, not the whole answer), while capturing tool names / cost / turns / the
    agent's own ``to_meeting`` intents for the driver. Never raises — a per-turn fault is returned
    as ``{"error": ...}`` so the driver degrades honestly."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

    tools: list[str] = []
    text = ""
    last_text = ""
    cost = 0.0
    turns = 0
    saw_result = False
    #: Elapsed s from turn start to the FIRST thing the room hears — the first streamed spoken
    #: sentence, or (fallback) the first ``to_meeting`` call. This is the REAL perceived latency:
    #: on the live path the sentence/POST leaves mid-turn, and the trailing wrap-up + result write +
    #: driver poll all come AFTER it, so the room never waits for them. 0.0 = never delivered.
    deliver_at = 0.0
    ttft = 0.0        # query → FIRST text delta (pure model time-to-first-token; profiling only)
    say_buf = ""      # accumulates streamed response prose until a sentence closes, then flushes to voice

    async def _flush_ready(*, final: bool = False) -> None:
        """Flush every complete sentence sitting in ``say_buf`` to the voice channel; on ``final``
        also flush the trailing partial clause so the answer's last words are never dropped. Each
        sentence is AWAITED in turn, so the room hears them strictly in order."""
        nonlocal say_buf, deliver_at
        while True:
            cut = _sentence_end(say_buf)
            if cut is None:
                break
            sentence, say_buf = say_buf[:cut].strip(), say_buf[cut:]
            if sentence:
                await _deliver_say(sentence)
                if not deliver_at:
                    deliver_at = round(time.monotonic() - turn_start, 2)
        if final and say_buf.strip():
            await _deliver_say(say_buf.strip())
            if not deliver_at:
                deliver_at = round(time.monotonic() - turn_start, 2)
            say_buf = ""

    _reset_intents()
    turn_start = time.monotonic()
    try:
        await client.query(prompt)
        async for msg in client.receive_response():
            # Partial-message stream events (include_partial_messages) carry the incremental text
            # deltas — the spoken answer AS it is typed. Detected structurally (``.event`` dict) so
            # we don't depend on the SDK's StreamEvent import path.
            ev = getattr(msg, "event", None)
            if isinstance(ev, dict):
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta", {}) or {}
                    if delta.get("type") == "text_delta":
                        if not ttft:
                            ttft = round(time.monotonic() - turn_start, 2)
                        say_buf += str(delta.get("text", "") or "")
                        await _flush_ready()
                continue
            if isinstance(msg, AssistantMessage):
                turns += 1
                # The agent is about to ACT (read/run/search). Speak whatever it just said FIRST — a
                # natural opener like "let me check…" — so the room hears it NOW (~1s), not after the
                # tool finishes. Force-flush because such an opener often ends in a terminator with no
                # trailing space (e.g. "…map.Then") that the streaming split leaves buffered.
                if any(isinstance(b, ToolUseBlock) for b in msg.content):
                    await _flush_ready(final=True)
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        name = str(block.name or "")
                        tools.append(name)
                        if not deliver_at and "to_meeting" in name:
                            deliver_at = round(time.monotonic() - turn_start, 2)
                    elif isinstance(block, TextBlock) and str(block.text or "").strip():
                        last_text = str(block.text).strip()
            elif isinstance(msg, ResultMessage):
                saw_result = True
                text = str(msg.result or "")
                cost = float(msg.total_cost_usd or 0.0)
                if msg.num_turns:
                    turns = int(msg.num_turns)
    except Exception as exc:  # noqa: BLE001 — a per-turn fault is an honest error, never a crash
        await _flush_ready(final=True)  # speak whatever was already composed before surfacing the fault
        return {"tools": tools, "text": last_text, "cost_usd": cost, "turns": turns,
                "error": str(exc) or exc.__class__.__name__, "deliver_at": deliver_at, "ttft": ttft,
                "sent": _parse_intents(_read_intents())}

    await _flush_ready(final=True)  # the closing partial sentence (no trailing terminator yet)
    intents = _parse_intents(_read_intents())
    # Salvage an abnormally-terminated turn's last prose (parity with ``_parse_stream``).
    if not saw_result and not text:
        text = last_text
    error = None
    if turns > 0 and not saw_result and not intents:
        error = "turn did not complete"
    return {"tools": tools, "text": text, "cost_usd": cost, "turns": turns,
            "error": error, "deliver_at": deliver_at, "ttft": ttft, "sent": intents}


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
        model=MODEL,                            # reliable default; heavy work escalates via sub-agents
        # The SDK default (12) is too small: a real ask explores the repo AND must still land the
        # final `to_meeting` call — the dry-run showed the agent hitting the cap mid-exploration and
        # delivering nothing. A larger budget lets it finish + deliver (the per-ask wall-clock is
        # still bounded by ASK_TIMEOUT_S on the driver side; a runaway can't stall the meeting).
        max_turns=int(os.environ.get("PROXY_MAX_TURNS", "40") or "40"),
        mcp_servers=_mcp_servers(),
        # Stream the model's text deltas as it generates so the host can speak each sentence the
        # instant it closes (first audio at the first clause, not the whole answer). Available since
        # claude-agent-sdk 0.1.48; keeps the MCP tools + the CLAUDE.md prime fully intact.
        include_partial_messages=True,
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
