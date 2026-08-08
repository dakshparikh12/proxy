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
  turn record the driver parses into a ``WorkroomResult``: ``{tools, text, cost_usd, turns, error,
  sent, deliver_at, ttft}``. Written atomically (temp + rename) so the poller never reads a half-file.
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
import shlex
import shutil
import time
import urllib.request
from typing import Any

#: The persistent session's default model. **Sonnet 5** — the current latency/cost sweet spot for
#: grounded agentic work (adaptive thinking baked into ``effort=high``, faster + cheaper than Opus).
#: Sonnet (not Haiku): the real-data dry-run proved Haiku OVER-EXPLORES a normal ask and exhausts the
#: turn budget WITHOUT calling ``to_meeting`` — it wakes but delivers nothing (a fast model that says
#: nothing is useless). Sonnet follows the "deliver in one turn" instruction reliably. Overridable via
#: ``PROXY_WORKROOM_MODEL`` (Law 4: no per-task model in code; the agent still escalates heavy work to
#: a stronger model through its own sub-agents). Fall back to ``claude-sonnet-4-6`` if a live replay
#: shows a quality regression on grounded work.
DEFAULT_MODEL = "claude-sonnet-5"

WAKE_IN = os.environ.get("PROXY_WAKE_IN", "/tmp/wake_in.jsonl")  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM
WAKE_OUT = os.environ.get("PROXY_WAKE_OUT", "/tmp/wake_out")  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM
MEETING_OUT = os.environ.get("PROXY_MEETING_OUT", "/tmp/to_meeting.jsonl")  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM
REPO_DIR = os.environ.get("PROXY_REPO_DIR", "/home/user/work/repo")
MCP_SERVER_FILE = os.environ.get("PROXY_MCP_SERVER", f"{REPO_DIR}/sandbox_meeting_mcp.py")
MODEL = (os.environ.get("PROXY_WORKROOM_MODEL", "") or DEFAULT_MODEL).strip()

#: Context7 (live library docs) as a PRE-WIRED convenience MCP — a "workshop" tool the agent can reach
#: for without discovery. OFF by default and gated on ONE founder-provisioned secret: set
#: ``CONTEXT7_API_KEY`` (from Secret Manager, injected into the sandbox env) to switch it on. It is
#: NOT wired unconditionally because it is DOUBLY egress-dependent — ``npx`` fetches
#: ``@upstash/context7-mcp`` from the npm registry AND every lookup calls ``https://context7.com/api``
#: — and read-egress from the sandbox is itself the founder-gated infra toggle (see the tool-workshop
#: note on ``_mcp_servers`` below). Wiring it while egress is denied would only stand up a stdio child
#: that fails at npx-fetch / on every call — a FAKE capability (Law 2). So it rides the same gate: a
#: key present asserts the founder has provisioned egress + the key, and only then does it load.
CONTEXT7_API_KEY = os.environ.get("CONTEXT7_API_KEY", "").strip()

#: SERENA (LSP-backed, SYMBOL-LEVEL code intel) as a PRE-WIRED stdio MCP server — a "workshop" tool
#: that lets the agent look up / navigate symbols (find_symbol, references, insert-at-symbol) instead
#: of reading whole files. On a big customer repo that is a real token + latency win: the agent goes
#: straight to a definition/callsite rather than grepping and reading megabytes to locate it.
#:
#: GATED ON REAL AVAILABILITY (Law 2 — never stand up a child that will fail = a FAKE capability). It
#: is wired ONLY when Serena is actually reachable in the sandbox, detected in order:
#:   1. ``PROXY_SERENA_CMD`` — an explicit full command line (e.g. the ``uvx --from git+…`` form) that
#:      the baked template knows works; used verbatim if set.
#:   2. ``PROXY_SERENA`` truthy — the baked per-repo template sets this to assert it installed Serena
#:      on PATH (``serena start-mcp-server``); trusted even if this process's PATH lookup lags.
#:   3. ``shutil.which("serena")`` — the binary is genuinely on PATH right now.
#: If none hold, Serena is SKIPPED SILENTLY (the base sandbox does not ship it, and egress is default-
#: deny so we can't uvx-fetch it at warm time — wiring it anyway would only fail at spawn).
#:
#: CACHE-SAFE BY CONSTRUCTION: Serena is wired WITHOUT ``alwaysLoad``, so on Sonnet 5 its many tool
#: schemas stay DEFERRED behind ToolSearch (auto tool-search) and never bloat the cached resident
#: prefix — unlike ``meeting`` (one tiny tool, alwaysLoad). The agent discovers Serena's tools on
#: demand when it actually needs symbol nav; a chit-chat turn never pays for them.
SERENA_CONTEXT = (os.environ.get("PROXY_SERENA_CONTEXT", "") or "ide-assistant").strip()

#: The meeting-wide thinking EFFORT — FIXED for the whole session so the CLI flags are byte-identical
#: every turn (a per-turn change would invalidate the resident-prime prompt cache mid-meeting). Adaptive
#: thinking (below) still decides PER ASK whether to think at all — hard asks think, trivial ones skip —
#: this only bounds HOW hard when it does. "high" is the sweet spot for grounded code work; overridable
#: via ``PROXY_WORKROOM_EFFORT`` (Law 4: no per-task effort in code; one constant for the meeting).
_EFFORT_ALLOWED = {"low", "medium", "high", "xhigh", "max"}
EFFORT = (os.environ.get("PROXY_WORKROOM_EFFORT", "") or "high").strip()
if EFFORT not in _EFFORT_ALLOWED:
    EFFORT = "high"

#: A per-meeting HARD cost ceiling in USD (``ClaudeAgentOptions.max_budget_usd``): the SDK stops a run
#: that exceeds it (``error_max_budget_usd``) instead of spending unboundedly. Set as a generous
#: RUNAWAY BACKSTOP, not a normal-operation limiter — a legitimate heavy code task must never die
#: mid-work — so it sits well above any real single-meeting spend; ``max_turns`` + the driver's
#: ASK_TIMEOUT already bound a normal turn. Overridable via ``PROXY_MAX_BUDGET_USD`` (the overnight
#: live-test caps TOTAL spend at the harness level; this is the per-meeting safety net). Unparsable
#: keeps the default.
try:
    MAX_BUDGET_USD = float(os.environ.get("PROXY_MAX_BUDGET_USD", "") or 20.0)
except ValueError:
    MAX_BUDGET_USD = 20.0

#: How long the host waits for the next wake line before re-polling (seconds). The session stays
#: warm across this idle; the driver's append is picked up within one beat.
_POLL_S = 0.1

#: The readiness breadcrumb IS the host's HEARTBEAT: a background task rewrites it on this cadence
#: for the whole life of the process — idle AND mid-turn — so its mtime keeps advancing while the
#: host is alive. The driver watches that mtime: a frozen breadcrumb (host OOM'd/SIGKILLed/hung) is
#: caught in seconds and triggers restart-and-retry, instead of the driver spinning the full
#: ASK_TIMEOUT_S on a dead host (up to 15 min of dead air on an ask). A genuinely-long WORKING turn
#: keeps this advancing (the heartbeat runs concurrently with the turn), so it is NOT mistaken for
#: dead. Kept well under the driver's dead-host budget so several beats land inside that window.
_HEARTBEAT_S = 2.0

#: OPENER SAFETY NET (Law 5, talk-and-glance / be present). On a heavy ask (e.g. a repo-wide rename)
#: adaptive thinking at ``EFFORT=high`` makes the model think hard BEFORE any text streams, and the
#: prime's "say a quick word first" is only soft guidance — nothing FORCES a first utterance. That let
#: a real ask sit ~86s in silence before first audio: a presence failure on exactly the ask where the
#: room needs reassurance. So if NO spoken content has left the sandbox by the time the model has
#: COMMITTED TO WORK, we emit ONE short canned acknowledgment, THEN let the real work + answer stream.
#: A SAFETY NET, not a replacement: the model's own opener suppresses it (no double-speak).
#:
#: THE TRIGGER IS "WORK HAS STARTED", NOT PURE WALL-CLOCK (the generalizable fix). A pure time budget
#: is un-tunable across repos/asks: on a well-mapped repo a judged answer streams its first words by
#: ~2.6s (TTFT 0.75-2.56s), but on a less-familiar repo or a harder-to-judge (messy) wake the SAME
#: kind of direct answer — or a silent cross-talk DECLINE — first thinks 6-12s before any token
#: (measured on gin: TTFT 6-12s, and the "staying quiet" turn thought 11.8s). A 4s wall-clock net fired
#: on ALL of those: redundant "On it…" in front of an answer a beat away, and — worse — a spurious
#: "On it…" on a turn that then chose SILENCE (cross-talk), so the room heard Proxy blurt filler on a
#: line that wasn't even addressed to it. The robust discriminator: a direct answer and a silent
#: decline emit NO tool call before their text; a genuinely heavy ask starts ACTING (Read/Bash/Grep/a
#: sub-agent) early. So the net fires once the model has (a) called its first real tool AND (b) still
#: spoken nothing after ``_OPENER_AFTER_TOOL_S`` — i.e. it is demonstrably off doing multi-step work
#: in silence, exactly when the room needs reassurance — never during the judge-then-answer/decline
#: phase. A pure-thinking heavy answer that emits NO tool for a long time is caught by the higher
#: ``_OPENER_HARD_FLOOR_S`` backstop (well above the judged-answer TTFT band, so it still never fires
#: on a normal answer/decline). ``PROXY_OPENER_BUDGET_S`` overrides the after-tool delay (kept name-
#: compatible); ``PROXY_OPENER_HARD_FLOOR_S`` overrides the no-tool backstop; unparsable keeps defaults.
try:
    _OPENER_AFTER_TOOL_S = float(os.environ.get("PROXY_OPENER_BUDGET_S", "") or 2.0)
except ValueError:
    _OPENER_AFTER_TOOL_S = 2.0
#: The no-tool backstop: even if the model never calls a tool, guarantee presence if it has been
#: silent this long (a rare pure-reasoning heavy answer). Set well ABOVE the observed judged-answer
#: TTFT band (6-12s) so it never pre-empts a normal answer/decline that is merely thinking.
try:
    _OPENER_HARD_FLOOR_S = float(os.environ.get("PROXY_OPENER_HARD_FLOOR_S", "") or 15.0)
except ValueError:
    _OPENER_HARD_FLOOR_S = 15.0
#: The single canned acknowledgment spoken by the safety net (kept short + generic — the real answer
#: follows immediately after). Deliberately not situation-specific (Law 4: no code maps ask→words).
_OPENER_TEXT = "On it — give me a moment."


#: THE SILENT-TURN SENTINEL (BUG 2). When the model judges no response is warranted (cross-talk, an
#: incidental "proxy", people talking among themselves), the prime instructs it to output NOTHING but
#: this one line. ``_run_turn`` detects it in the FIRST streamed content and suppresses ALL voice
#: delivery for the turn (no opener, no TTS, no relay say) while the record still captures the reasoning
#: for the trace. Matched case- and whitespace-insensitively so a stray capital/space never leaks a
#: spoken "staying silent" into the room (the exact live BUG-2 failure). Kept as a plain sentinel token
#: (physics), not a situation→action rule — WHAT counts as silence is still the agent's live judgement.
SILENT_SENTINEL = "[SILENT]"


def _is_silent_sentinel(text: str) -> bool:
    """True iff ``text`` (the turn's streamed content so far) is JUST the silent sentinel — the model
    chose to stay quiet. Robust to case + surrounding whitespace/newlines so a stray capital or a
    leading space never lets a spoken 'staying silent' leak into the room (the live BUG-2 failure)."""
    return text.strip().upper() == SILENT_SENTINEL


def _could_be_silent_sentinel(text: str) -> bool:
    """True while ``text`` (the turn's streamed content so far) could STILL become the silent sentinel
    — i.e. its stripped, upper-cased form is a prefix of ``[SILENT]`` (including empty / whitespace).
    Used to HOLD the first streamed tokens without speaking them until the content either completes the
    sentinel (⇒ stay silent) or diverges into real prose (⇒ speak it as a normal turn). Robust to case
    + leading whitespace so no partial 'staying silent' ever leaks to voice (the live BUG-2 failure)."""
    head = text.strip().upper()
    return SILENT_SENTINEL.startswith(head)


#: PRIME-THE-CACHE-DURING-PREP. The big resident prefix (behavioral prime + interaction layer +
#: codebase understanding + MCP tool schemas) pays a one-time ``cache_creation`` on the FIRST request
#: of the session; every later wake is a cheap ``cache_read``. Left alone, that one-time cost lands on
#: the first REAL wake — exactly when the room is waiting. So BEFORE serving any wake we fire ONE
#: synthetic warm-up turn that reuses the SILENT-turn machinery: it is instructed to emit exactly the
#: ``[SILENT]`` sentinel, so ``_run_turn`` suppresses all voice/relay delivery and the room hears
#: NOTHING. As a belt-and-suspenders guarantee the room can't be touched, ``_prime_cache`` also
#: temporarily removes the relay env for the warm-up (see below). Best-effort — a failure never blocks
#: serving. Disable with ``PROXY_PRIME_CACHE=0`` (e.g. if a deployment prefers not to spend the warm-up).
_PRIME_CACHE = os.environ.get("PROXY_PRIME_CACHE", "1").strip().lower() not in {"0", "false", "no", "off"}
_PRIME_PROMPT = (
    "Internal warm-up ping before the meeting starts — no one has addressed you and there is nothing "
    "to do. Do NOT use any tool, do NOT read or explore anything. Reply with this one exact line and "
    "nothing else:\n"
    f"{SILENT_SENTINEL}"
)

#: Bare-URL matcher for the voice sanitizer (BUG 1): an ``http(s)://…`` or ``www.…`` run of non-space
#: characters. TTS must NEVER speak a URL verbatim (the live failure: it read a whole weather.gov query
#: string aloud). We DROP the URL rather than try to pronounce it — the prime already tells the agent to
#: put links/detail in chat; this is the physics net for when a URL slips into the spoken stream anyway.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
#: Markdown link ``[label](url)`` → keep only the human ``label`` (the URL is never spoken).
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((?:[^)]*)\)")
#: Inline markdown emphasis/code syntax the voice must not pronounce as literal characters
#: (``**bold**`` → ``bold``, ``` `code` ``` → ``code``, ``__x__`` / ``*x*`` / ``_x_`` / ``~~x~~``).
#: We strip the SYNTAX only — never try to render markdown beautifully as speech (kept simple + general).
_MD_SYNTAX_RE = re.compile(r"(\*\*|__|~~|`|(?<![A-Za-z0-9])[*_](?![*_ ]))")
#: A leading markdown block marker on a line: heading ``#``, blockquote ``>``, or a list bullet
#: (``- ``/``* ``/``+ ``/``1. ``). Dropped so the voice doesn't speak "hash" / "greater than" / "dash".
_MD_BLOCK_RE = re.compile(r"^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)")


def _sanitize_for_voice(text: str) -> str:
    """Make ONE chunk safe to SPEAK: strip markdown syntax and never hand a raw URL to TTS (BUG 1).

    Two layers protect the room; this is the PHYSICS net (the prime is the other). It does NOT try to
    read markdown beautifully — it only ensures the room never hears literal syntax or a URL read out
    character-by-character (the live failure: TTS spoke ``**82°F**`` and a whole forecast.weather.gov
    query string). Order matters: markdown links collapse to their label first (so the label survives
    while its URL is dropped), THEN any remaining bare URL is removed, THEN inline/block syntax is
    stripped. Collapses the whitespace a dropped URL leaves. Returns ``""`` when nothing speakable is
    left (e.g. a lone URL) — the caller simply skips an empty chunk. Pure string physics (Law 4)."""
    if not text:
        return text
    out = _MD_LINK_RE.sub(r"\1", text)        # [label](url) → label
    out = _URL_RE.sub(" ", out)               # drop any remaining bare URL (never spoken)
    out = _MD_BLOCK_RE.sub("", out)           # strip a leading heading/quote/bullet marker
    out = _MD_SYNTAX_RE.sub("", out)          # strip inline **/`/_/~ emphasis+code syntax
    # Collapse the runs of whitespace a dropped URL / stripped marker leaves, so the voice doesn't
    # pause oddly; keep it a single-line spoken clause.
    return re.sub(r"\s{2,}", " ", out).strip()


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


async def _deliver_say(content: str) -> bool:
    """Stream ONE spoken sentence to the room the instant it closes — the voice channel itself.

    LIVE (``PROXY_MEETING_RELAY`` set): POST it to the host relay right now, mid-turn, so the room
    hears sentence 1 while the model is still writing sentence 3. The blocking ``urllib`` POST runs
    off the event loop (``to_thread``) but is AWAITED, so sentences reach the room strictly in order
    without stalling the token stream. PROOF/file mode: append it to the intents file (medium
    ``say``) so the driver's ``sent`` reflects exactly what was spoken, in order.

    Never raises. Returns whether the sentence was DELIVERED: ``True`` on a good relay POST (or a
    file-mode append, which the driver replays), ``False`` when the LIVE relay POST FAILED — the room
    did NOT hear this sentence. That honest boolean is what lets the caller surface a swallowed miss
    (Law 2): a failed POST is still recorded locally as a ``relay_error`` line for the trace, but the
    turn no longer reports it as a clean delivered success. An empty (fully-sanitized-away) chunk is
    nothing to speak — not a failure — so it returns ``True``."""
    # PHYSICS voice safety net (BUG 1): never hand markdown syntax or a raw URL to TTS. Sanitize at
    # the exact boundary where text meets the voice channel; a chunk that was ONLY a URL sanitizes to
    # empty and is skipped (nothing to speak) rather than spoken as silence-of-characters.
    content = _sanitize_for_voice(content)
    if not content:
        return True
    rec: dict[str, Any] = {"ts": time.time(), "content": content, "medium": "say", "to": ""}
    relay = os.environ.get("PROXY_MEETING_RELAY", "").strip()
    if relay:
        try:
            await asyncio.to_thread(_relay_post, relay, rec)
            return True
        except Exception as exc:  # noqa: BLE001 — never crash the agent's turn on a send fault
            # The room did NOT hear this sentence. Record the miss locally for the trace, then report
            # the FAILURE to the caller so it can degrade honestly (never a silent "delivered").
            rec = {**rec, "relay_error": str(exc)}
            try:
                with open(MEETING_OUT, "a", encoding="utf-8") as f:  # noqa: PTH123 — tiny append, in-sandbox
                    f.write(json.dumps(rec) + "\n")
            except OSError:
                pass
            return False
    try:
        with open(MEETING_OUT, "a", encoding="utf-8") as f:  # noqa: PTH123 — tiny append, in-sandbox
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return True


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
    #: Has ANY spoken content left the sandbox this turn — the model's own prose OR the canned opener?
    #: The opener safety net only fires while this is still False (so the model's own opener suppresses
    #: it). Flipped to True right BEFORE each real ``_deliver_say`` await, so the check-then-set stays
    #: atomic under single-threaded asyncio (no await between test and set).
    spoke = False
    opener_fired = False  # the canned opener has been emitted (fired at most once per turn)
    #: HONEST DELIVERY (Law 2). Latches True if the LIVE relay POST for any spoken ANSWER sentence
    #: FAILED — the room did not hear that content, yet the turn otherwise "succeeds" (no error). The
    #: driver reads this to degrade honestly instead of reporting a swallowed miss as a delivered
    #: success. Only real answer prose (``_flush_ready``) counts; the canned presence opener is filler
    #: whose miss must not spuriously degrade a turn whose real answer then delivers fine.
    delivery_failed = False
    #: BUG 2 — the SILENT-TURN gate. When the model's FIRST streamed content is the silent sentinel
    #: (``[SILENT]``), the whole turn is a judged non-response (cross-talk / incidental "proxy"): NO
    #: voice delivery, NO opener — the room hears nothing — while the record still captures the
    #: reasoning for the trace. ``silent`` latches True once the streamed content settles as the
    #: sentinel; ``sentinel_maybe`` is the "still could be the sentinel" hold-state so we buffer the
    #: first tokens (``[`` … ``[SILENT]``) WITHOUT speaking them until it either completes the sentinel
    #: (⇒ silent) or diverges into real prose (⇒ a normal turn, flush what we held).
    silent = False
    sentinel_maybe = True
    #: Has the model called its first REAL tool this turn (Read/Bash/Grep/Write/a sub-agent — anything
    #: but ``to_meeting``, which is delivery, not work)? This is the "committed to multi-step work"
    #: signal the opener gates on: a direct answer or a silent cross-talk decline never calls a tool
    #: before its text, so the opener never fires on them; a genuinely heavy ask starts acting early.
    tool_started = False

    async def _flush_ready(*, final: bool = False) -> None:
        """Flush every complete sentence sitting in ``say_buf`` to the voice channel; on ``final``
        also flush the trailing partial clause so the answer's last words are never dropped. Each
        sentence is AWAITED in turn, so the room hears them strictly in order.

        A SILENT turn (BUG 2) never flushes: the model chose the sentinel, so the buffered content is
        internal reasoning the room must not hear. A turn still in the ``sentinel_maybe`` hold-state
        also doesn't flush yet — the streamed content could still complete the sentinel; only once it
        has diverged into real prose (``sentinel_maybe`` cleared) is the held buffer spoken."""
        nonlocal say_buf, deliver_at, spoke, delivery_failed
        if silent or sentinel_maybe:
            return
        while True:
            cut = _sentence_end(say_buf)
            if cut is None:
                break
            sentence, say_buf = say_buf[:cut].strip(), say_buf[cut:]
            if sentence:
                spoke = True  # real prose is going out ⇒ suppress the canned opener from here on
                if not await _deliver_say(sentence):
                    delivery_failed = True  # the room did not hear this answer sentence (Law 2)
                if not deliver_at:
                    deliver_at = round(time.monotonic() - turn_start, 2)
        if final and say_buf.strip():
            spoke = True
            if not await _deliver_say(say_buf.strip()):
                delivery_failed = True  # the closing clause never reached the room (Law 2)
            if not deliver_at:
                deliver_at = round(time.monotonic() - turn_start, 2)
            say_buf = ""

    async def _opener_watchdog() -> None:
        """The presence safety net, gated on WORK-COMMITTED not pure wall-clock. Poll until EITHER the
        model has committed to multi-step work (its first real tool call) and stayed silent
        ``_OPENER_AFTER_TOOL_S`` past it, OR it has been silent the ``_OPENER_HARD_FLOOR_S`` no-tool
        backstop — then emit ONE canned acknowledgment so a genuinely-working turn isn't dead air.

        WHY GATE ON A TOOL CALL, not time alone: a direct answer and a silent cross-talk decline emit
        NO tool before their text, and their first token can lag 6-12s on a less-familiar repo / a hard
        judgment — a pure time budget fired canned filler in front of the answer, or (worse) spoke
        'On it…' on a turn that then chose SILENCE. Tying the net to 'a tool has started' makes it fire
        ONLY when the model is demonstrably off doing work, exactly when the room needs reassurance, and
        never during the judge-then-answer/decline phase. Suppressed the instant the model's OWN words
        stream (``spoke`` / ``ttft`` / ``say_buf``): its opener always wins (no double-speak). The
        check-then-set of ``spoke``/``opener_fired`` is atomic (no await before the set), so it can
        never double-speak with a concurrently-flushing real sentence."""
        nonlocal spoke, opener_fired, deliver_at
        tool_seen_at: float | None = None
        try:
            while True:
                # The model has spoken / is mid-stream ⇒ its own words are imminent; stand down forever.
                # A SILENT turn (BUG 2) also stands the opener down — the turn is a judged non-response,
                # so the room must hear NOTHING, not a canned "on it…".
                if spoke or opener_fired or ttft or say_buf or silent:
                    return
                now = time.monotonic()
                if tool_started and tool_seen_at is None:
                    tool_seen_at = now  # start the short after-work-began grace
                due = (
                    (tool_seen_at is not None and now - tool_seen_at >= _OPENER_AFTER_TOOL_S)
                    or (now - turn_start >= _OPENER_HARD_FLOOR_S)
                )
                if due:
                    break
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            return
        # Re-check atomically after the loop's last await: the model may have just started speaking.
        if spoke or opener_fired or ttft or say_buf:
            return
        spoke = True
        opener_fired = True
        # The canned opener is presence FILLER, not the answer — a miss on it must not degrade a turn
        # whose real answer then delivers fine, so its delivery result is intentionally not latched.
        await _deliver_say(_OPENER_TEXT)
        if not deliver_at:
            deliver_at = round(time.monotonic() - turn_start, 2)

    _reset_intents()
    turn_start = time.monotonic()
    watchdog = asyncio.create_task(_opener_watchdog())
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
                        # BUG 2 — silent-turn gate. Resolve the sentinel hold-state as content grows:
                        # while the streamed content could STILL be the sentinel, hold (don't speak);
                        # once it settles as exactly the sentinel the turn is SILENT (never speak);
                        # once it diverges into real prose, it's a normal turn (flush what we held).
                        if sentinel_maybe:
                            if _is_silent_sentinel(say_buf):
                                silent = True
                            elif not _could_be_silent_sentinel(say_buf):
                                sentinel_maybe = False
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
                        # First REAL tool (anything but the delivery channel) ⇒ the model has committed
                        # to multi-step work: arm the presence opener (see ``_opener_watchdog``).
                        if "to_meeting" not in name:
                            tool_started = True
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
                # PROOF instrumentation: is the cache actually engaging (resident prefix reused,
                # not re-parsed)? cache_read growing turn-over-turn = caching works.
                u = getattr(msg, "usage", None) or {}
                _g = u.get if isinstance(u, dict) else (lambda k, d=0: getattr(u, k, d))
                print(f"[usage] cache_read={_g('cache_read_input_tokens', 0)} "
                      f"cache_write={_g('cache_creation_input_tokens', 0)} "
                      f"input={_g('input_tokens', 0)} output={_g('output_tokens', 0)}", flush=True)
    except Exception as exc:  # noqa: BLE001 — a per-turn fault is an honest error, never a crash
        watchdog.cancel()
        await _flush_ready(final=True)  # speak whatever was already composed before surfacing the fault
        return {"tools": tools, "text": last_text, "cost_usd": cost, "turns": turns,
                "error": str(exc) or exc.__class__.__name__, "deliver_at": deliver_at, "ttft": ttft,
                "sent": _parse_intents(_read_intents()), "delivery_failed": delivery_failed}

    watchdog.cancel()
    await _flush_ready(final=True)  # the closing partial sentence (no trailing terminator yet)
    intents = _parse_intents(_read_intents())
    # Salvage an abnormally-terminated turn's last prose (no ``result`` event but prose was streamed).
    if not saw_result and not text:
        text = last_text
    error = None
    if turns > 0 and not saw_result and not intents:
        error = "turn did not complete"
    return {"tools": tools, "text": text, "cost_usd": cost, "turns": turns,
            "error": error, "deliver_at": deliver_at, "ttft": ttft, "sent": intents,
            "delivery_failed": delivery_failed}


def _beat() -> None:
    """Bump the readiness breadcrumb's mtime — one heartbeat tick. Rewriting the file (not just
    ``os.utime``) is simplest and portable; the content (the launch timestamp) is unchanged. The
    driver reads the mtime, so a fresh write = a live host. Best-effort: a write fault is swallowed
    (the NEXT tick retries; the driver only concludes 'dead' after MANY missed ticks)."""
    try:
        (pathlib.Path(WAKE_OUT) / "_host.ready").write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


async def _heartbeat() -> None:
    """Advance the readiness/heartbeat breadcrumb forever, CONCURRENTLY with the serve loop — so its
    mtime keeps moving even while a single long turn is running (a real multi-minute code task must
    NOT look dead). If this task's process is SIGKILLed/OOM'd the ticks simply stop and the file goes
    stale, which is exactly the dead-host signal the driver watches for."""
    while True:
        _beat()
        await asyncio.sleep(_HEARTBEAT_S)


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
            # BUG 5 — QUEUE LATENCY. The warm session is ONE ClaudeSDKClient: it runs one query at a
            # time, so a wake enqueued while a prior turn is in flight WAITS here (an honest, documented
            # single-flight constraint). Measure that wait: the driver stamps ``queued_at`` (wall s)
            # when it appended the wake; the gap until we start serving it is the host-side queue time
            # the live battery asserts on. Robust to an absent/old ``queued_at`` (⇒ 0.0).
            try:
                queued_at = float(req.get("queued_at", 0.0) or 0.0)
            except (TypeError, ValueError):
                queued_at = 0.0
            queued_ms = round(max(0.0, time.time() - queued_at) * 1000.0, 1) if queued_at else 0.0
            record = await _run_turn(client, prompt)
            record["queued_ms"] = queued_ms
            record["_served_at"] = time.time()
            _write_result(wake_id, record)
        await asyncio.sleep(_POLL_S)


def _serena_server() -> dict[str, Any] | None:
    """Serena's stdio MCP config, or ``None`` when Serena is not genuinely available (skip silently).

    Honest gate (Law 2): only returns a config when Serena can actually run in this sandbox —
    an explicit ``PROXY_SERENA_CMD``, the template's ``PROXY_SERENA`` assertion, or the ``serena``
    binary on PATH. The base sandbox ships none of these, so by default this returns ``None`` and no
    child is spawned. ``--project REPO_DIR`` pins Serena at the cloned repo regardless of the stdio
    child's cwd (robust vs. relying on an inherited cwd). No ``alwaysLoad`` — see ``SERENA_CONTEXT``:
    the tools stay deferred so they never touch the cached prefix."""
    override = os.environ.get("PROXY_SERENA_CMD", "").strip()
    if override:
        try:
            parts = shlex.split(override)
        except ValueError:
            return None
        if not parts:
            return None
        return {"type": "stdio", "command": parts[0], "args": parts[1:]}
    optin = os.environ.get("PROXY_SERENA", "").strip().lower() in {"1", "true", "yes", "on"}
    if optin or shutil.which("serena"):
        return {
            "type": "stdio",
            "command": "serena",
            "args": ["start-mcp-server", "--context", SERENA_CONTEXT, "--project", REPO_DIR],
        }
    return None


def _mcp_servers() -> dict[str, Any]:
    """The meeting MCP server config for the WARM session — the SAME stdio server the cold path
    registered via ``.mcp.json``, so ``to_meeting`` loads ONCE for the whole session. The relay envs
    (``PROXY_MEETING_RELAY``/``_TOKEN``/``_OUT``) are already in this process's env and inherited by
    the stdio child, so a live turn's ``to_meeting`` reaches the host exactly as before.

    TOOL-WORKSHOP HONESTY (Law 2): the agent keeps its FULL native toolset regardless (Read/Grep/
    Bash/Write/Edit/Glob/WebSearch/WebFetch/Task sub-agents) — see the ``ClaudeAgentOptions`` note in
    ``main`` — so this dict only adds PRE-WIRED convenience MCP servers on top. The one non-meeting
    server we can offer, Context7 (live library docs), is gated: it loads ONLY when ``CONTEXT7_API_KEY``
    is set, because it is egress-dependent (npx-fetch + a live API call per lookup) and read-egress is
    the founder-gated infra toggle. Standing it up while egress is denied would fake a capability, so
    it stays off until the key (which implies egress + the secret are provisioned) is present."""
    # ``alwaysLoad: true`` — the meeting server's ONE tool (``to_meeting``) skips tool-search
    # deferral, so it is ALWAYS immediately callable, never behind a ToolSearch/MCPSearch round-trip.
    # WHY THIS MATTERS (measured, generalizable): Claude Code turns on MCP tool-search auto-mode by
    # default — when tool descriptions exceed ~10% of the context window the tools are DEFERRED and
    # must be discovered via a ToolSearch call before use. In every delivering quality-battery trace
    # the agent spent one whole tool round-trip on ``ToolSearch`` right before its FIRST ``to_meeting``
    # call — pure latency on the CRITICAL delivery path of EVERY task, plus a reliability risk (if the
    # search fails to surface it, the room hears nothing). ``to_meeting`` is the agent's SOLE line to
    # the room; it must never be deferred. Scoped to THIS server only, so tool-search still lazy-loads
    # everything else (the workshop stays intact). This makes the prime's "already loaded, don't search
    # for it" literally TRUE (Law 4: a wiring fact, not a situation→action rule).
    servers: dict[str, Any] = {
        "meeting": {"type": "stdio", "command": "python3", "args": [MCP_SERVER_FILE],
                    "alwaysLoad": True},
    }
    if CONTEXT7_API_KEY:
        # Pre-wired only when the founder has provisioned the key (⇒ egress + secret are in place).
        servers["context7"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp", "--api-key", CONTEXT7_API_KEY],
        }
    # SERENA (symbol-level code intel) — added ONLY when actually available (see ``_serena_server``),
    # and deliberately WITHOUT ``alwaysLoad`` so its tool schemas stay deferred (cache-safe on Sonnet 5).
    serena = _serena_server()
    if serena is not None:
        servers["serena"] = serena
    return servers


async def _prime_cache(client: Any) -> None:
    """Fire ONE synthetic silent warm-up so the resident-prefix cache is CREATED before the first real
    wake (which then pays only a fast ``cache_read``). Reuses ``_run_turn`` + the ``[SILENT]`` sentinel
    so nothing is spoken; ALSO strips the relay env for the duration as a hard guarantee the warm-up
    can never reach the room even if the model doesn't emit the sentinel. Best-effort — any failure is
    swallowed so a prime hiccup never blocks serving. Runs on the SAME single-flight client, so it is
    AWAITED to completion before ``_serve`` issues the first real query (no concurrent-query conflict)."""
    if not _PRIME_CACHE:
        return
    # HARD guarantee the warm-up cannot touch the room: remove the relay so ``_deliver_say`` falls back
    # to a local file append (which the next real turn's ``_reset_intents`` clears) instead of POSTing.
    saved_relay = os.environ.pop("PROXY_MEETING_RELAY", None)
    try:
        rec = await _run_turn(client, _PRIME_PROMPT)
        print(f"[prime] cache warmed (turns={rec.get('turns')} err={rec.get('error')})", flush=True)
    except Exception as exc:  # noqa: BLE001 — priming is best-effort; never block serving on it
        print(f"[prime] warm-up skipped: {type(exc).__name__}: {exc}", flush=True)
    finally:
        if saved_relay is not None:
            os.environ["PROXY_MEETING_RELAY"] = saved_relay
        _reset_intents()  # discard any local intent lines the warm-up produced (clean first real wake)


async def main() -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    # THE TOOL-WORKSHOP GUARANTEE (do NOT add ``allowed_tools``/``tools``/``disallowed_tools`` here).
    # The workshop metaphor in the prime ("reach for the best tool… if you don't have it, get it") is
    # only HONEST if the agent actually holds its full native toolset. It does, precisely BECAUSE these
    # three params are left UNSET:
    #   * ``tools`` unset (None) ⇒ the CLI default = the WHOLE Claude Code toolset (Read, Grep, Glob,
    #     Bash, Write, Edit, WebSearch, WebFetch, the Task sub-agent tool, …). Setting it to ``[]``
    #     would DISABLE all tools; a curated list would shrink the workshop to that list.
    #   * ``allowed_tools`` unset ⇒ it is only an AUTO-APPROVE allowlist, it does NOT remove tools from
    #     the toolset (per the claude-agent-sdk docs). With ``bypassPermissions`` below every tool is
    #     already auto-approved, so leaving it unset is exactly "full toolset, no prompts".
    #   * ``disallowed_tools`` unset ⇒ nothing is blocked. (The Law-3 world-touching boundary is NOT
    #     enforced by blocking tools — it is enforced structurally: the sandbox holds no push/send
    #     credentials, so Bash/Write can only touch the sandbox, and irreversible acts must be staged
    #     as an ``offer`` the host applies behind a human click.)
    # HONEST LIMIT ON "get more tools" — the founder-gated infra toggle: the agent's ability to
    # actually ACQUIRE a tool it lacks (WebSearch/WebFetch, ``pip install X``, ``npx some-mcp``, the
    # pre-wired Context7 above) needs outbound READ egress from the sandbox. Egress is default-DENY
    # today (write egress stays blocked forever — that IS the credential boundary). So while egress is
    # denied the workshop is "use the tools you have (the full native set, on the repo already cloned
    # in) — but you can't reach the internet to fetch more". Flipping ON read-egress (a founder infra
    # decision, writes still blocked) is the ONE toggle that turns "get more tools" from aspiration
    # into fact; the prime is written to be honest about exactly this ("if you have internet, get more").
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
        # A per-meeting HARD cost ceiling (see ``MAX_BUDGET_USD``): the SDK stops a run that exceeds it
        # rather than spending unboundedly — a runaway backstop, set well above any real single-meeting
        # spend so it never bites a legitimate heavy task.
        max_budget_usd=MAX_BUDGET_USD,
        # DETERMINISTIC TOOL SURFACE: only the MCP servers we pass HERE load — the CLI ignores any
        # discovered ``.mcp.json`` / user connectors, so the warm session can't pick up a surprise
        # server that would waste tokens/turns OR (worse) change the byte-stable tool schemas and
        # invalidate the resident-prime cache mid-meeting. ``to_meeting`` is passed via ``_mcp_servers``
        # below, so it still loads (verified in the real-inference smoke); this only closes the door on
        # UNdeclared servers. Native built-in tools (Read/Bash/…) are unaffected — the workshop stays whole.
        strict_mcp_config=True,
        mcp_servers=_mcp_servers(),
        # ADAPTIVE thinking: Claude decides PER ASK whether — and how much — to think. A trivial ask
        # ("mute yourself") skips thinking entirely (no TTFT hit), a hard code task thinks hard. FIXED
        # for the meeting (adaptive is a mode, not a per-turn value; paired with the constant EFFORT
        # below) so the CLI flags never change turn-to-turn and the resident-prime cache stays warm.
        # display defaults to "omitted" — the reasoning is NOT streamed as text_delta, so it never
        # reaches ``say_buf`` and is never spoken (the receive loop only flushes text_delta anyway;
        # this keeps it out at the source). Sends ``--thinking adaptive`` (claude-agent-sdk ≥0.2.115).
        thinking={"type": "adaptive"},
        # One meeting-wide effort ceiling (``--effort``), FIXED so it never invalidates the cache.
        effort=EFFORT,  # type: ignore[arg-type]  # constrained to _EFFORT_ALLOWED above
        # Stream the model's text deltas as it generates so the host can speak each sentence the
        # instant it closes (first audio at the first clause, not the whole answer). Available since
        # claude-agent-sdk 0.1.48; keeps the MCP tools + the CLAUDE.md prime fully intact.
        include_partial_messages=True,
        # MARATHON CONDENSING SAFETY NET (SPEC §3): the whole transcript accumulates in this warm
        # session's cached conversation (each wake inlines only the delta), so a very long meeting
        # would eventually approach the context window. We do NOT hand-roll a condenser — the Claude
        # Agent SDK's built-in AUTOCOMPACTION is exactly the spec's "quiet condensing of the oldest
        # transcript ONLY if it grows huge": near the window it summarizes the OLDEST history and keeps
        # recent turns verbatim. It is ON by default (``isAutoCompactEnabled``); we deliberately leave
        # it enabled (no ``extra_args``/settings disable it) rather than duplicate it — simplest, and
        # it preserves the resident-recall property (the summary stays in the same cached conversation).
    )
    async with ClaudeSDKClient(options=options) as client:
        # Mark the host READY so the driver can tell the warm session came up (vs. a startup fault);
        # this same breadcrumb is then kept fresh by the heartbeat as a liveness signal.
        try:
            pathlib.Path(WAKE_OUT).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        # PRIME THE CACHE before marking ready: fire one silent warm-up so the big resident prefix pays
        # its one-time cache_creation NOW (during prep, off the room's critical path) — the first REAL
        # wake is then a fast cache_read. Awaited to completion (single-flight client), then we mark
        # ready. Best-effort: a warm-up fault is swallowed and we serve anyway. Provision's readiness
        # wait (_WARM_PROVISION_WAIT_S) generously covers this small extra delay before the first wake.
        await _prime_cache(client)
        _beat()
        # Run the heartbeat CONCURRENTLY with serving so the breadcrumb's mtime keeps advancing while
        # the host is alive — idle or mid-turn. If the process dies (OOM/SIGKILL) both tasks stop and
        # the file goes stale, which the driver detects in seconds (fast recover vs. 15 min of silence).
        heartbeat = asyncio.create_task(_heartbeat())
        try:
            await _serve(client)
        finally:
            heartbeat.cancel()


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
