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

import asyncio
import json
import logging
import os
import pathlib
import shlex
import time
import uuid
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
#: The Claude Agent SDK the WARM session host uses (ONE persistent ClaudeSDKClient per meeting).
#: Pinned to the spike-confirmed floor; the sandbox pip-installs it alongside the MCP SDK.
SDK_PIN = "claude-agent-sdk>=0.2.115"

# ── The WARM PERMANENT SESSION (the #1 latency fix) ───────────────────────────────────────────
# ``session_host.py`` runs a SINGLE persistent native-Claude session INSIDE the sandbox, started at
# provision so it is warm before the first wake. Each wake is a file round-trip to it (~1-3s) instead
# of a cold ``claude -p`` spawn (~11-13s: re-spawns the MCP server, re-discovers the tool, reloads the
# prime EVERY turn). The driver (:meth:`Workroom.run_ask`) writes the wake to WAKE_IN and polls
# WAKE_OUT/<id>.json; if the host isn't up/responding it FALLS BACK to the cold path (honest degrade).
SESSION_HOST_FILE = f"{REPO_DIR}/session_host.py"
WAKE_IN = "/tmp/wake_in.jsonl"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
WAKE_OUT = "/tmp/wake_out"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
#: The host's readiness breadcrumb (written once its persistent session is open); its presence lets
#: the driver skip the warm attempt entirely when the session never came up (straight to cold).
HOST_READY_FILE = f"{WAKE_OUT}/_host.ready"
#: Where the host's stdout/stderr land (a fault breadcrumb for diagnosis; never on the meeting path).
SESSION_HOST_LOG = "/tmp/session_host.log"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
#: Poll cadence for the warm round-trip. The turn itself is bounded by ASK_TIMEOUT_S (once the host
#: is READY); a host that never comes up is caught by the SHORT readiness gate below, not that budget.
_WARM_POLL_S = 0.25
#: How long the FIRST wake waits for the host's readiness breadcrumb before giving up on warm and
#: falling back to cold. Generous enough for the persistent session to open (the SDK client + the MCP
#: stdio child), short enough that a never-started host degrades quickly. Only paid until the host is
#: seen ready ONCE (then :attr:`Workroom._host_ready` short-circuits it for the rest of the meeting).
#: In production the host is started at provision (which itself takes tens of seconds for clone +
#: install), so by the first wake it is long ready and this returns on the first poll — the budget
#: only bites a host that failed to open (→ fast cold fallback). ``PROXY_WARM_READY_TIMEOUT_S``
#: overrides for a slow bake; an unset/unparsable value keeps the default.
try:
    _WARM_READY_TIMEOUT_S = float(os.environ.get("PROXY_WARM_READY_TIMEOUT_S", "") or 30.0)
except ValueError:
    _WARM_READY_TIMEOUT_S = 30.0

#: How long PROVISION waits for the warm host to open (before the meeting goes live), so the FIRST
#: wake finds it ready instead of racing the SDK-client+prime open and cold-degrading for the whole
#: meeting. Provision happens on join (before anyone addresses Proxy), so this wait is transparent —
#: it just moves the ~15-30s warm-up off the critical path of the first response. Best-effort: if the
#: host isn't ready in this budget, provision returns anyway and the first wake retries warm → cold.
try:
    _WARM_PROVISION_WAIT_S = float(os.environ.get("PROXY_WARM_PROVISION_WAIT_S", "") or 90.0)
except ValueError:
    _WARM_PROVISION_WAIT_S = 90.0

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
    #: Elapsed seconds from turn start to the agent's FIRST ``to_meeting`` call — the moment the room
    #: actually hears/sees the answer on the LIVE (relay) path, where the in-sandbox MCP server POSTs
    #: on the tool call itself (mid-turn). The trailing wrap-up turn + result write + driver poll all
    #: come AFTER this and the room never waits for them, so this — NOT the full ``run_ask`` wall
    #: time — is the real perceived latency. 0.0 = never delivered / cold path (not instrumented).
    deliver_at: float = 0.0
    #: Query → the agent's FIRST streamed text token (pure model time-to-first-token). The floor of
    #: ``deliver_at``: perceived latency can't beat it. Isolates model/connection TTFT from the extra
    #: time to compose the first whole sentence. 0.0 = not streamed (cold path / no spoken text).
    ttft: float = 0.0
    #: The agent's OWN recorded ``to_meeting`` intents (content/medium/to), read from the sandbox's
    #: local JSONL in the no-relay/file path. Each is a channel choice the agent made this turn; the
    #: session replays them over the connection honoring the chosen medium (NOT ``text``).
    sent: list[dict[str, Any]] = field(default_factory=list)


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
    #: Whether the WARM session host was started at provision. True ⇒ :meth:`run_ask` tries the warm
    #: round-trip first (falling back to the cold ``claude -p`` path on any miss). False ⇒ cold only.
    warm: bool = False
    #: Latches True once the host's readiness breadcrumb is first seen — so only the FIRST wake pays
    #: the readiness wait; every later wake goes straight to the (warm) turn.
    _host_ready: bool = False

    @property
    def sandbox_id(self) -> str:
        return str(getattr(self.sandbox, "sandbox_id", "") or "")

    async def _run(self, cmd: str, *, timeout: float, envs: dict[str, str] | None = None,
                   background: bool = False) -> Any:
        """One command in the sandbox, through the seam.

        ``background=True`` starts a DETACHED long-running process (the E2B native background mode):
        the run returns a handle immediately without waiting for the process to exit — this is how the
        WARM session host is launched so it keeps living after provision returns (a plain ``nohup … &``
        still blocks E2B's command handle until timeout)."""
        if background:
            outcome = await self.call(
                lambda: self.sandbox.commands.run(cmd, background=True, envs=envs or {}),
                service="e2b",
            )
        else:
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

    async def run_ask(self, ask: str, *, recent: str = "") -> WorkroomResult:
        """Wake Claude in the workroom on ONE reactive ask; return the result.

        Prefers the WARM permanent session (``session_host.py``, started at provision): the prime +
        the ``to_meeting`` MCP tool are already loaded, so a wake is ~1-3s instead of a ~11-13s cold
        ``claude -p`` spawn. The wake prompt (the exact instructions below) is handed to the warm
        session; its result is parsed into the SAME :class:`WorkroomResult`. If the warm host isn't
        up/responding (never started, crashed, or the round-trip times out) this FALLS BACK to the
        cold ``claude -p`` path — an honest degrade, never a crash. Either path reaches the room only
        through the agent's ONE ``to_meeting`` tool (speak/chat/dm/screen/offer/mute), relayed live to
        the host connection (or recorded locally when there is no relay).

        Never raises — a failed turn is an honest ``WorkroomResult.error`` (§9)."""
        prompt = self._wake_prompt(ask, recent)
        if self.warm:
            warm = await self._run_ask_warm(ask, prompt)
            if warm is not None:
                return warm
            logger.warning("warm session miss for meeting ask — falling back to cold claude -p")
        return await self._run_ask_cold(ask, prompt)

    @staticmethod
    def _wake_prompt(ask: str, recent: str = "") -> str:
        """The one wake prompt (judge-if-addressed → decline false wake → deliver in one turn →
        output-excellence → offer). Built once here so the WARM and COLD paths hand Claude the SAME
        instructions — the delivery mechanism changed, the behavior contract did not. The recent
        transcript is INLINED so a wake needs no ./MEETING_NOTES.md read just to judge/answer (a
        turn saved every wake); the full history stays on disk for the rare older-context case."""
        recent_block = (
            "Recent transcript (most recent lines — use this DIRECTLY; you do NOT need to open "
            f"./MEETING_NOTES.md unless you need OLDER context than this):\n{recent}\n\n"
            if recent.strip() else ""
        )
        return (
            "Someone in the room may have addressed you (your name was heard). Your identity and how "
            "to behave are in ./CLAUDE.md; older meeting history (if you need it) is in "
            "./MEETING_NOTES.md.\n\n"
            f"{recent_block}"
            f"The line that woke you:\n{ask}\n\n"
            "FIRST judge whether you were genuinely being addressed — make this call FAST, from the "
            "recent transcript above ALONE, before you read any code or investigate anything. If 'proxy' was used "
            "incidentally (e.g. \"our proxy server\", \"the proxy pool\") or people are talking among "
            "themselves and no one is actually asking you for anything, STOP IMMEDIATELY — do nothing, "
            "don't call the tool, don't explore the repo. Only start real work once you're sure you "
            "were really addressed.\n\n"
            "If you were addressed, do exactly as much as the ask needs — a greeting or a quick "
            "question is one direct reply with no tools; a real task gets the real work (read the "
            "ACTUAL code, run code to verify, draft real files). SPEAK by simply writing your reply — "
            "your words are spoken to the room live, streamed sentence by sentence as you type. Use "
            "your `mcp__meeting__to_meeting` tool (call it by that exact name — already loaded, don't "
            "search for it) ONLY for the non-spoken channels: chat/dm/screen/offer/mute. CRITICAL: you "
            "get exactly ONE turn — there is NO 'later', no follow-up turn, no coming back. Never say "
            "\"I'll bring it back\" / \"give me a few minutes\" / \"will follow up\" and then stop — "
            "that leaves the room with nothing. Do the ENTIRE task now (a longer one may open with a "
            "one-line \"on it…\", but you must then finish it), and before you stop you MUST have "
            "delivered the real result/artifact to the room in this same turn. For anything "
            "world-touching, produce the real artifact and, as your final step, offer it "
            "(medium='offer') — you have no push/send credentials by design, so the offer IS the "
            "delivery."
        )

    def _wake_envs(self) -> dict[str, str]:
        """The subscription auth + the relay wiring the in-sandbox MCP server needs. When ``relay_url``
        is empty the server appends intents to ``PROXY_MEETING_OUT`` (no live relay) — honest degrade."""
        return {
            "CLAUDE_CODE_OAUTH_TOKEN": self.token,
            "PROXY_MEETING_RELAY": self.relay_url,
            "PROXY_MEETING_TOKEN": self.relay_token,
            "PROXY_MEETING_OUT": TO_MEETING_OUT,
        }

    async def _await_host_ready(self, *, timeout: float | None = None) -> bool:
        """Wait (bounded) for the warm host's readiness breadcrumb, then latch ``_host_ready``.

        The persistent session takes a beat to open (the SDK client + the MCP stdio child + loading
        the prime). Called at PROVISION with a generous ``timeout`` to warm the host BEFORE the meeting
        goes live (so no wake races it); and by the first wake as a fallback (default budget). A host
        that never comes up returns False so the wake degrades to cold. NEVER raises."""
        if self._host_ready:
            return True
        deadline = time.monotonic() + (timeout if timeout is not None else _WARM_READY_TIMEOUT_S)
        while time.monotonic() < deadline:
            try:
                raw = getattr(
                    await self.call(lambda: self.sandbox.files.read(HOST_READY_FILE), service="e2b"),
                    "value", "",
                )
            except Exception:  # noqa: BLE001 — not ready yet (file absent) → keep polling
                raw = ""
            if raw:
                self._host_ready = True
                return True
            await asyncio.sleep(_WARM_POLL_S)
        return False

    async def _run_ask_warm(self, ask: str, prompt: str) -> WorkroomResult | None:
        """Serve the wake on the WARM permanent session (``session_host.py``): confirm the host is
        ready, append the wake to ``WAKE_IN`` with a fresh id, then poll ``WAKE_OUT/<id>.json``
        (bounded by ``ASK_TIMEOUT_S`` once the turn is running).

        Returns the parsed :class:`WorkroomResult` on success, or ``None`` on ANY miss (host never
        came up, the round-trip timed out, a write/read fault) — a ``None`` tells :meth:`run_ask` to
        fall back to the cold path. NEVER raises. If the host wrote an honest per-turn ``error`` we
        still surface it as a real result (that IS the answer — the session degrades, no cold retry)."""
        if not await self._await_host_ready():
            return None  # the warm host never opened → cold fallback (fast)
        wake_id = uuid.uuid4().hex
        req = json.dumps({"id": wake_id, "prompt": prompt})
        try:
            # Append the wake line (the host tails WAKE_IN). ``printf '%s\n' <arg>`` writes the JSON
            # verbatim + one newline; ``shlex.quote`` safely quotes the payload's embedded quotes so
            # exactly one valid JSON line lands (no partial/concatenated line the host could mis-read).
            await self._run(
                f"mkdir -p {shlex.quote(WAKE_OUT)} && "
                f"printf '%s\\n' {shlex.quote(req)} >> {shlex.quote(WAKE_IN)}",
                timeout=30.0,
            )
        except Exception:  # noqa: BLE001 — a write fault → fall back to cold (never crash)
            logger.exception("warm wake enqueue failed — falling back to cold")
            return None
        out_path = f"{WAKE_OUT}/{wake_id}.json"
        deadline = time.monotonic() + ASK_TIMEOUT_S
        start = time.monotonic()
        while time.monotonic() < deadline:
            try:
                raw = getattr(
                    await self.call(lambda: self.sandbox.files.read(out_path), service="e2b"),
                    "value", "",
                )
            except Exception:  # noqa: BLE001 — not written yet (or a transient read) → keep polling
                raw = ""
            if raw:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    return None  # a corrupt result → cold fallback (never present garbage)
                return WorkroomResult(
                    ask=ask,
                    text=str(rec.get("text", "") or ""),
                    tools=[str(t) for t in (rec.get("tools") or [])],
                    turns=int(rec.get("turns", 0) or 0),
                    cost_usd=float(rec.get("cost_usd", 0.0) or 0.0),
                    error=(str(rec["error"]) if rec.get("error") else None),
                    deliver_at=float(rec.get("deliver_at", 0.0) or 0.0),
                    ttft=float(rec.get("ttft", 0.0) or 0.0),
                    sent=[dict(s) for s in (rec.get("sent") or []) if isinstance(s, dict)],
                )
            # Adaptive backoff: poll fast at first so a quick reply lands snappily, then EASE OFF so a
            # multi-minute task (PRD, deep code) doesn't hammer the E2B files API hundreds of times
            # (~720 reads over 3 min at a flat 0.25s → E2B contention/cancellation under load). This
            # cuts the long-task read count ~8x while keeping short-wake latency low.
            elapsed = time.monotonic() - start
            # Tighter backoff than before (was 1.0/2.0) so a finished turn is DETECTED fast — the
            # poll lag is pure latency after Claude is done. Still eases off on long tasks to avoid
            # hammering the E2B files API: 0.25s snappy start, 0.5s mid, 1.0s for multi-minute work.
            await asyncio.sleep(_WARM_POLL_S if elapsed < 5.0 else (0.5 if elapsed < 30.0 else 1.0))
        return None  # timed out waiting on the warm host → cold fallback

    async def _run_ask_cold(self, ask: str, prompt: str) -> WorkroomResult:
        """The COLD path (fallback / when no warm host): spawn a fresh ``claude -p`` per wake with the
        meeting MCP server (``--mcp-config``). Slower (~11-13s) but self-contained — the honest degrade
        when the warm session is unavailable. Never raises — a fault becomes ``WorkroomResult.error``."""
        cmd = (
            # Reset the per-turn intent log FIRST: the in-sandbox MCP server only APPENDS to
            # TO_MEETING_OUT, so without this each cold wake would re-read wakes 1..N-1's intents
            # and double-send them. ``sent`` must be exactly THIS turn's ``to_meeting`` calls
            # (mirrors the warm host's per-turn reset).
            f"rm -f {shlex.quote(TO_MEETING_OUT)} && "
            f"cd {shlex.quote(self.repo_dir)} && "
            f"claude -p {shlex.quote(prompt)} "
            f"--mcp-config {shlex.quote(MCP_CONFIG_FILE)} --dangerously-skip-permissions "
            f"--output-format stream-json --verbose > /tmp/ask.jsonl 2>/tmp/ask.err; echo DONE"
        )
        try:
            await self._run(cmd, timeout=ASK_TIMEOUT_S, envs=self._wake_envs())
            raw = getattr(await self.call(lambda: self.sandbox.files.read("/tmp/ask.jsonl"),  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
                                          service="e2b"), "value", "")
            # The agent's OWN channel choices this turn, as the in-sandbox MCP server recorded them
            # to $PROXY_MEETING_OUT (the no-relay/file path). A MISSING file is the NORMAL silence
            # case — the agent chose not to call to_meeting (e.g. declined a false wake), so the MCP
            # server never created it. Treat that as "no intents", NEVER an error: otherwise a correct
            # silence would surface a spurious "I hit a problem" degrade into a room Proxy stayed out of.
            try:
                intents_raw = getattr(await self.call(lambda: self.sandbox.files.read(TO_MEETING_OUT),
                                                      service="e2b"), "value", "")
            except Exception:  # noqa: BLE001 — absent intent file = clean silence, not a fault
                intents_raw = ""
            return _parse_stream(ask, raw or "", intents_raw or "")
        except Exception as exc:  # noqa: BLE001 — never crash the loop
            logger.exception("workroom run_ask (cold) failed")
            return WorkroomResult(ask=ask, error=str(exc) or exc.__class__.__name__)

    async def pause(self) -> str | None:
        """Pause the sandbox at teardown so a warm per-repo snapshot can RESUME in ~1s (vs a cold
        clone+install). Returns the paused sandbox id on success, or ``None`` when the E2B SDK exposes
        no clean pause (then the caller simply tears down). NEVER raises — pause is an optimization.

        Resume is wired in :func:`provision_workroom` via ``resume_id`` (``Sandbox.connect(id)``)."""
        pause = getattr(self.sandbox, "pause", None)
        if pause is None:
            return None
        try:
            await self.call(lambda: pause(), service="e2b")
            return self.sandbox_id or None
        except Exception:  # noqa: BLE001 — pause is best-effort; a fault just means "tear down cold"
            logger.warning("workroom pause failed (will tear down instead)", exc_info=True)
            return None

    async def teardown(self) -> None:
        """Kill the sandbox (meeting end / cleanup)."""
        try:
            await self.call(lambda: self.sandbox.kill(), service="e2b")
        except Exception:  # noqa: BLE001
            logger.exception("workroom teardown kill failed")


def _parse_intents(raw: str) -> list[dict[str, Any]]:
    """Parse the in-sandbox MCP server's recorded ``to_meeting`` intents (one JSON object per line)
    into ``[{content, medium, to}]`` — the agent's OWN channel choices in the no-relay/file path.
    Skips relay-error/malformed lines silently (the local record is best-effort)."""
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


def _parse_stream(ask: str, raw: str, intents_raw: str = "") -> WorkroomResult:
    """Parse ``claude`` stream-json output → the ordered tool names + the final result text, and
    fold in the agent's recorded ``to_meeting`` intents. Detects ABNORMAL TERMINATION: a clean turn
    always emits a ``result`` event; if there were assistant turns but no ``result`` (crash/OOM) and
    no recorded intents, mark it an honest ``error`` so the session can degrade without silence.

    ``text`` carries ONLY the model's own ``result`` event (never salvaged from mid-turn assistant
    prose): that prose is internal scratchpad the agent did NOT choose to say to the room, so the
    session must not speak it — an errored turn with no recorded intent gets a bare honest apology,
    not the agent's private notes (soft Law 2)."""
    tools: list[str] = []
    text = ""
    cost = 0.0
    turns = 0
    saw_result = False
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
            saw_result = True
            text = str(ev.get("result", "") or "")
            cost = float(ev.get("total_cost_usd", 0.0) or 0.0)
    intents = _parse_intents(intents_raw)
    error = None
    if turns > 0 and not saw_result and not intents:
        error = "turn did not complete"
    return WorkroomResult(ask=ask, text=text, tools=tools, turns=turns, cost_usd=cost,
                          error=error, sent=intents)


def _packaged_source(name: str) -> str:
    """A packaged script's source, read off disk next to this module — the ONE source of truth for a
    file COPIED into the sandbox verbatim at provision (the script runs where the workspace is not
    installed, so it can't be imported there)."""
    return (pathlib.Path(__file__).with_name(name)).read_text(encoding="utf-8")


def _mcp_server_source() -> str:
    """The packaged in-sandbox MCP server source (``sandbox_meeting_mcp.py``)."""
    return _packaged_source("sandbox_meeting_mcp.py")


def _session_host_source() -> str:
    """The packaged WARM session-host source (``session_host.py``) — the ONE persistent Claude
    session that serves each wake without a cold spawn."""
    return _packaged_source("session_host.py")


async def _start_session_host(wr: Workroom) -> bool:
    """Start the WARM permanent session host as a BACKGROUND process in the sandbox, warm before the
    first wake. Best-effort: on any fault we leave ``warm=False`` and the meeting runs the cold path.

    The host is ``nohup``-detached (survives the provision command's return), its stdout/stderr land
    in ``SESSION_HOST_LOG`` (a fault breadcrumb), and it inherits the subscription auth + the relay
    wiring so a warm turn's ``to_meeting`` reaches the host exactly as the cold path's does. Returns
    True iff the launch command ran (not that the session is fully open — the driver's first wake
    detects a dead host by timing out and falling back)."""
    envs = {
        **wr._wake_envs(),  # noqa: SLF001 — same module: auth + relay wiring the host inherits
        "PROXY_REPO_DIR": wr.repo_dir,
        "PROXY_MCP_SERVER": MCP_SERVER_FILE,
        "PROXY_WAKE_IN": WAKE_IN,
        "PROXY_WAKE_OUT": WAKE_OUT,
    }
    # Prepare the ready/out dirs synchronously, THEN start the host as a native E2B background
    # process (detached; the run returns a handle at once). ``nohup … &`` alone still blocks E2B's
    # command handle to the timeout, so ``background=True`` is what keeps the session alive at join.
    try:
        await wr._run(  # noqa: SLF001 — same module
            f"mkdir -p {shlex.quote(WAKE_OUT)} && rm -f {shlex.quote(HOST_READY_FILE)}",
            timeout=30.0,
        )
        start = (
            f"python3 {shlex.quote(SESSION_HOST_FILE)} "
            f"> {shlex.quote(SESSION_HOST_LOG)} 2>&1"
        )
        await wr._run(start, timeout=0.0, envs=envs, background=True)  # noqa: SLF001 — same module
    except Exception:  # noqa: BLE001 — the warm host is an optimization; a launch fault = cold path
        logger.warning("warm session host launch failed — meeting runs the cold path", exc_info=True)
        return False
    logger.info("warm session host launched (sandbox=%s)", wr.sandbox_id)
    return True


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
    resume_id: str | None = None,
) -> Workroom:
    """Provision + seed a per-meeting workroom, warm and ready before the first ask.

    Provisions an E2B sandbox (pre-baked ``template`` when available), clones the repo in
    (shallow), ensures native ``claude`` is present, and seeds the prime (CLAUDE.md), the map
    (REPO_MAP.md), and an empty transcript file. It ALSO wires the agent's ONE meeting interface:
    pip-installs the pinned ``mcp`` + ``claude-agent-sdk``, writes the ``sandbox_meeting_mcp.py``
    server in, drops a ``.mcp.json`` pointing native ``claude`` at it (stdio), AND starts the WARM
    permanent session (``session_host.py``) in the background so the first wake is served on an
    already-loaded session (~1-3s vs a ~11-13s cold spawn). ``relay_url`` / ``relay_token`` are
    stashed on the :class:`Workroom` for the MCP server's relay envs; empty ⇒ no relay (intents
    recorded locally). Egress stays default-deny; the ONLY credential injected is the subscription
    ``CLAUDE_CODE_OAUTH_TOKEN`` (no push/send creds — the Law-3 gate by construction). All E2B
    round-trips ride ``call`` (the call_external seam).

    ``resume_id`` (if set) RESUMES a paused per-repo snapshot via ``Sandbox.connect(id)`` (a warm
    repo+deps snapshot resumes in ~1s vs a cold clone+install); on a resume fault it degrades to a
    fresh cold provision. This is the E2B pause/resume fast-join lever (paired with
    :meth:`Workroom.pause`).
    """
    if sandbox_class is None:
        # The full path (not ``http.external``) so our ``http`` package never shadows the
        # stdlib ``http`` — the codebase-wide convention for the call_external seam.
        from libs.http.src.http.external import e2b_sandbox_class

        sandbox_class = e2b_sandbox_class()

    sandbox = None
    resumed = False
    if resume_id:
        # Fast join: reconnect to a paused warm snapshot (repo + deps already baked in). A resume
        # fault degrades to a fresh provision below — never a crash, never a stuck meeting.
        connect = getattr(sandbox_class, "connect", None)
        if connect is not None:
            try:
                outcome = await call(lambda: connect(resume_id, timeout=int(PROVISION_TIMEOUT_S)),
                                     service="e2b", unit_cost_usd=0.0)
                sandbox = getattr(outcome, "value", outcome)
                resumed = sandbox is not None
            except Exception:  # noqa: BLE001 — resume miss → fall through to a cold provision
                logger.warning("workroom resume(%s) failed — provisioning fresh", resume_id,
                               exc_info=True)
                sandbox = None
    if sandbox is None:
        create_kwargs: dict[str, Any] = {"timeout": int(PROVISION_TIMEOUT_S)}
        if template:
            create_kwargs["template"] = template
        outcome = await call(lambda: sandbox_class.create(**create_kwargs), service="e2b",
                             unit_cost_usd=0.0)
        sandbox = getattr(outcome, "value", outcome)
    wr = Workroom(sandbox=sandbox, call=call, token=token,
                  relay_url=relay_url, relay_token=relay_token)

    # Setup (idempotent; a pre-baked template / resumed snapshot makes most of this a no-op/instant):
    #  - shallow-clone the repo into REPO_DIR (fast; ~6s on cal.com)
    #  - ensure native claude is installed
    #  - install the pinned mcp SDK + the claude-agent-sdk the WARM session host needs
    depth = "--depth 1" if sha is None else ""
    setup = (
        f"mkdir -p {shlex.quote(WORKROOM_ROOT)} && "
        f"(test -d {shlex.quote(REPO_DIR)}/.git || git clone {depth} {shlex.quote(repo_url)} "
        f"{shlex.quote(REPO_DIR)}) && "
        f"(command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code) && "
        f"pip3 install -q {shlex.quote(MCP_PIN)} {shlex.quote(SDK_PIN)}"
    )
    await wr._run(setup, timeout=PROVISION_TIMEOUT_S)  # noqa: SLF001 — same module
    if sha:
        await wr._run(f"cd {shlex.quote(REPO_DIR)} && git checkout -q {shlex.quote(sha)} || true",
                      timeout=120.0)  # noqa: SLF001
    # Seed the orientation files.
    # Prime (CLAUDE.md) and map (REPO_MAP.md) are SEPARATE files. NOTE: inlining the 13KB map into
    # CLAUDE.md was tried and REVERTED — it made every turn process a huge context (warm latency
    # ~2x worse) AND diluted the lean behavioral prime (cross-talk over-fire regression). Keeping the
    # prime lean is what makes the model actually follow it; the map is read on demand.
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
    # Write + START the WARM permanent session (the #1 latency fix): one persistent Claude session
    # per meeting, warm before the first wake. On any launch fault ``warm`` stays False and run_ask
    # uses the cold path (honest degrade).
    await wr._write_file(SESSION_HOST_FILE, _session_host_source())  # noqa: SLF001
    wr.warm = await _start_session_host(wr)
    if wr.warm:
        # Warm the host DURING provision (before anyone addresses Proxy) so the first wake finds it
        # ready — otherwise it races the host's ~15-30s SDK-client+prime open, times out, and cold-
        # degrades for the WHOLE meeting (+11-13s per wake). This moves the warm-up off the first
        # response's critical path. Best-effort: not-ready-in-budget just leaves the first wake to
        # retry warm then cold-degrade. THE key latency fix (cold→warm).
        ready = await wr._await_host_ready(timeout=_WARM_PROVISION_WAIT_S)  # noqa: SLF001
        logger.info("warm host readiness at provision: sandbox=%s ready=%s", wr.sandbox_id, ready)
    logger.info("workroom provisioned: sandbox=%s repo=%s relay=%s warm=%s resumed=%s",
                wr.sandbox_id, repo_url, bool(relay_url), wr.warm, resumed)
    return wr
