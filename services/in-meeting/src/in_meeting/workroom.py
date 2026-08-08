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
import re
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# The richer PRIME lives in ``in_meeting.prime``. Production seeds THAT prime as CLAUDE.md so native
# Claude knows how it talks (Design B): it SPEAKS by writing its reply (streamed to the room), and
# uses the ONE ``to_meeting`` tool only for the non-spoken channels (chat/dm/screen/offer/mute/
# unmute). There is no second, weaker prime here.
from .prime import WORKROOM_PRIME

logger = logging.getLogger(__name__)

#: Where the repo + seed files live inside the sandbox (the user's home; root paths are
#: not writable by the default sandbox user — verified).
WORKROOM_ROOT = "/home/user/work"
REPO_DIR = f"{WORKROOM_ROOT}/repo"
#: The crash/reconnect RECOVERY record of the transcript (SPEC §3). Recall is resident (the warm
#: session's cache, fed the delta per wake), NOT this file — it exists only to re-seed context if the
#: warm session is lost/restarted, so a woken turn never reads it just to know what was said.
TRANSCRIPT_FILE = f"{REPO_DIR}/MEETING_NOTES.md"
MAP_FILE = f"{REPO_DIR}/REPO_MAP.md"
PRIME_FILE = f"{REPO_DIR}/CLAUDE.md"
#: The INTERACTION LAYER — worked examples of how Proxy shows up in the room + the above-and-beyond
#: quality bar + how to reason out the unpredicted. It ships as its OWN editable markdown file
#: (``interaction_layer.md``, packaged beside this module) and is seeded into the sandbox next to
#: CLAUDE.md, which pulls it in with a native ``@`` import — so the layer rides the resident prime
#: exactly like the prime does, but stays a separate file the founder can edit without touching the
#: prime. It is CRAFT (rich examples), distinct from the short behavioral law in ``prime.py``.
INTERACTION_LAYER_NAME = "interaction_layer.md"
INTERACTION_LAYER_FILE = f"{REPO_DIR}/INTERACTION_LAYER.md"
#: The ROSTER — who is in the room (title/agenda/participants + the DM-id note), rendered by
#: ``prime.render_meeting_info`` and seeded as part of the ONE boot-verified seed set (below), so the
#: file the prime tells the agent to read is never silently missing. Folded in from the provisioner's
#: former separate best-effort write. ``MEETING_INFO_FILE`` here is the full sandbox path (the
#: basename lives in ``prime.MEETING_INFO_FILE``).
MEETING_INFO_FILE = f"{REPO_DIR}/MEETING_INFO.md"
#: The honest placeholder seeded as MEETING_INFO.md when no roster is known yet — the file is ALWAYS
#: present (part of the boot-verified set) but never carries a fabricated roster (Law 1/2).
_MEETING_INFO_PLACEHOLDER = "# Meeting\n\n(no meeting metadata available)\n"
#: The skills pack — procedural know-how loaded by native ``claude`` only WHEN doing that kind of
#: work (``.claude/skills/<name>/SKILL.md`` is the CLI's discovery path), keeping the always-on prime
#: lean. Seeded at provision alongside CLAUDE.md. The names are the packaged source dirs under
#: ``skills/`` beside this module; each carries one ``SKILL.md``.
SKILLS_DIR = f"{REPO_DIR}/.claude/skills"
SKILL_NAMES = ("meeting-artifact", "meeting-diagram", "background-job")
#: The known sandbox file the warm session writes its own Claude session id into at open, so the
#: fork-resume advanced option (``claude -p --resume <id> --fork-session …``) in the interaction
#: layer is actually usable. Written by ``session_host`` on the first turn; read by the agent.
SESSION_ID_FILE = f"{REPO_DIR}/.proxy_session_id"

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
# WAKE_OUT/<id>.json; if the host isn't up/responding it RESTARTS the host and retries the warm turn
# ONCE, then honest-degrades — there is ONE delivery path (no separate cold engine).
SESSION_HOST_FILE = f"{REPO_DIR}/session_host.py"
WAKE_IN = "/tmp/wake_in.jsonl"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
WAKE_OUT = "/tmp/wake_out"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
#: The host's readiness breadcrumb (written once its persistent session is open); its presence lets
#: the driver latch readiness so only the first wake pays the readiness wait.
HOST_READY_FILE = f"{WAKE_OUT}/_host.ready"
#: Where the host's stdout/stderr land (a fault breadcrumb for diagnosis; never on the meeting path).
SESSION_HOST_LOG = "/tmp/session_host.log"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
#: Poll cadence for the warm round-trip. The turn itself is bounded by ASK_TIMEOUT_S (once the host
#: is READY); a host that never comes up is caught by the SHORT readiness gate below, not that budget.
_WARM_POLL_S = 0.25

#: The DEAD-HOST budget (seconds) — the short window in which a wake must see EITHER its result OR a
#: living host, before the driver stops waiting and restarts-and-retries. It is DISTINCT from
#: ASK_TIMEOUT_S (the ceiling for a genuinely-long *working* turn): a live host keeps advancing its
#: heartbeat breadcrumb (``session_host`` rewrites HOST_READY_FILE every ~2s, even mid-turn), so a
#: long turn is NEVER mistaken for dead — only a host whose heartbeat has FROZEN for this whole window
#: (OOM/SIGKILL/hang) is. This turns "up to 15 min of dead air on a crashed host" into a few-second
#: recover. Wide enough (relative to the host's ~2s heartbeat) to tolerate transient E2B read
#: hiccups; short enough that a crash mid-ask recovers in seconds. ``PROXY_DEAD_HOST_TIMEOUT_S``
#: overrides; an unset/unparsable value keeps the default.
try:
    _DEAD_HOST_TIMEOUT_S = float(os.environ.get("PROXY_DEAD_HOST_TIMEOUT_S", "") or 20.0)
except ValueError:
    _DEAD_HOST_TIMEOUT_S = 20.0
#: How long a wake waits for the host's readiness breadcrumb before giving up on the (current) warm
#: session. Generous enough for the persistent session to open (the SDK client + the MCP stdio
#: child), short enough that a never-started host is caught quickly (→ restart-and-retry). Only paid
#: until the host is seen ready ONCE (then :attr:`Workroom._host_ready` short-circuits it for the
#: rest of the meeting; a restart clears the latch so the fresh host's readiness is re-checked).
#: In production the host is started at provision (which itself takes tens of seconds for clone +
#: install), so by the first wake it is long ready and this returns on the first poll — the budget
#: only bites a host that failed to open. ``PROXY_WARM_READY_TIMEOUT_S`` overrides for a slow bake;
#: an unset/unparsable value keeps the default.
try:
    _WARM_READY_TIMEOUT_S = float(os.environ.get("PROXY_WARM_READY_TIMEOUT_S", "") or 30.0)
except ValueError:
    _WARM_READY_TIMEOUT_S = 30.0

#: How long PROVISION waits for the warm host to open (before the meeting goes live), so the FIRST
#: wake finds it ready instead of racing the SDK-client+prime open and cold-degrading for the whole
#: meeting. Provision happens on join (before anyone addresses Proxy), so this wait is transparent —
#: it just moves the ~15-30s warm-up off the critical path of the first response. Best-effort: if the
#: host isn't ready in this budget, provision returns anyway and the first wake waits, then
#: restarts-and-retries if still not ready.
try:
    _WARM_PROVISION_WAIT_S = float(os.environ.get("PROXY_WARM_PROVISION_WAIT_S", "") or 90.0)
except ValueError:
    _WARM_PROVISION_WAIT_S = 90.0

#: Timeouts (seconds). The sandbox itself outlives any single ask (keep-warm heartbeat
#: bumps it); a single ask is bounded so a runaway turn can't stall the meeting.
PROVISION_TIMEOUT_S = 1800.0
ASK_TIMEOUT_S = 900.0

#: The default pre-baked E2B template (repo + node + claude + deps warm). Until it is baked,
#: ``None`` provisions a base sandbox and sets it up at warm time (proven path). The bake spec that
#: PRODUCES such a template (toolchain + Serena + ast-grep + deps as image layers) is checked in as
#: ``services/in-meeting/e2b.Dockerfile`` (+ ``e2b.toml``); once a customer template is baked, its id
#: is passed per-provision via the ``template`` arg — this default stays ``None`` so the base-sandbox
#: warm-time-setup path (the proven, working default) is UNCHANGED until a template is deliberately wired.
DEFAULT_TEMPLATE: str | None = None

#: PAUSE/RESUME FAST-JOIN — the biggest latency/cost lever, GATED OFF by default. When ON, a PRE-PRIMED
#: sandbox (repo cloned, files seeded, warm session + built cache) is ``beta_pause``d at teardown and a
#: later meeting RESUMES it via ``AsyncSandbox.connect(id)`` in ~1s instead of a cold clone+build. It is
#: OFF by default because E2B persistence is PUBLIC BETA with a known multi-cycle bug (E2B issue #884:
#: file changes may not persist after the 2nd+ resume) — so it needs live multi-cycle validation before
#: it can be trusted on a real meeting. With the flag OFF the working path is byte-for-byte unchanged:
#: ``pause`` is a no-op (tear down cold) and any ``resume_id`` is IGNORED (fresh cold provision). Turn it
#: on ONLY after validation via ``PROXY_ENABLE_PAUSE_RESUME=1``. Even ON, resume degrades to a cold
#: provision on any fault — the meeting never blocks on a bad snapshot (Law 2: no fake fast-path).
def _pause_resume_enabled() -> bool:
    return os.environ.get("PROXY_ENABLE_PAUSE_RESUME", "").strip().lower() in {"1", "true", "yes", "on"}

#: The delimiter that OPENS the resident codebase-understanding block appended to the prime
#: (CLAUDE.md). The symbol map (real file:line, ranked) lives here so it is part of the CACHED
#: prime — the agent's understanding is RESIDENT, not a REPO_MAP.md it must Read every wake. Kept
#: visually distinct from the behavioral prime above it (a clear header + a one-line contract) so
#: the lean behavior isn't diluted. The prime text (``prime.py``) points HERE, not at ./REPO_MAP.md.
UNDERSTANDING_HEADER = (
    "\n\n# Your understanding of this codebase\n"
    "(Your resident mental model — a holistic, qualitative comprehension of this codebase. It is NOT "
    "a line-number index: use it to understand the system and to know WHICH area/module to go to. "
    "When you need to cite an exact `file:line`, look it up LIVE with a quick grep/read at that "
    "location — never cite a line from memory.)\n\n"
)

#: The native ``@`` import that pulls the INTERACTION LAYER file into CLAUDE.md. Claude Code resolves
#: ``@path`` imports in CLAUDE.md relative to the file, so ``@./INTERACTION_LAYER.md`` loads the layer
#: file seeded beside CLAUDE.md — the layer rides the resident prime exactly like the prime, while
#: staying a separate, easily-editable file (the founder edits ``interaction_layer.md``, not the
#: prime). Composed AFTER the behavioral prime and BEFORE the guardrail-last invariant, so the
#: injection guardrail is still the strict final word of the composed CLAUDE.md.
INTERACTION_LAYER_IMPORT = (
    "\n\n# How you show up in the room (worked examples + the quality bar)\n"
    "Your craft — concrete examples of what the room expects, the above-and-beyond quality bar, and "
    "how to reason out anything not covered — is imported here; read it as part of who you are:\n\n"
    "@./INTERACTION_LAYER.md\n"
)


def compose_resident_prime(prime: str, map_text: str) -> str:
    """Compose the CLAUDE.md that is seeded RESIDENT into the warm session: the lean behavioral
    ``prime`` followed by the codebase-understanding block (the comprehension-first resident doc —
    the qualitative mental model + a compact navigation aid, under :data:`UNDERSTANDING_HEADER`),
    and finally the SHARED prompt-injection guardrail as the strict LAST word.

    This whole text becomes the cached prime, so the agent's understanding rides in-context every
    wake with ZERO reads — resident, not a file. Exact ``file:line`` citations are grounded LIVE at
    answer time (Law 1), using the understanding to know where to look. An empty ``map_text`` appends
    no understanding block (a clean degrade), but the guardrail is ALWAYS present.

    The guardrail (``agentkit.with_injection_guardrail`` — the ONE shared body, no per-service copy)
    is appended LAST so it is the final authoritative word of the CLAUDE.md that native Claude reads:
    transcript content — which now accumulates resident in the conversation — is UNTRUSTED DATA, never
    instructions (Hard Rule: prompt safety / SPEC §3.10). Placing it after the understanding block
    keeps it strictly last, so nothing later in the prime (or injected via transcript data) can lift it."""
    # Import from the fully-typed deep module (``agentkit.guardrails``), not the opaque facade
    # (``agentkit``) — so mypy --strict sees the real ``-> str`` signature (matches how the
    # premeeting map-build imports ``agentkit.provider``). ONE shared body, no per-service copy.
    from agentkit.guardrails import with_injection_guardrail

    # The behavioral prime, THEN the native @import of the interaction layer (craft/examples/quality
    # bar — its own editable file), THEN the resident codebase understanding (when a map is present).
    body = prime + INTERACTION_LAYER_IMPORT
    if map_text.strip():
        body += UNDERSTANDING_HEADER + map_text.rstrip() + "\n"
    # ``with_injection_guardrail`` is declared ``-> str``; the cross-member ``agentkit.*`` import is
    # opaque to the strict walk (``ignore_missing_imports``), so the return is seen as Any — the value
    # is a real ``str`` (matches the sandbox_meeting_mcp convention for the same cross-boundary case).
    return with_injection_guardrail(body)  # type: ignore[no-any-return]

# The E2B command/file round-trips ride this seam signature: a thunk returning an awaitable,
# wrapped by ``call_external`` (retry + telemetry). Injected so tests pass a fake.
Seam = Callable[..., Awaitable[Any]]


def _poll_cancel_is_spurious() -> bool:
    """True if a ``CancelledError`` just raised from an E2B POLL read is a spurious TRANSPORT cancel
    (swallow it — keep polling), False if THIS task is genuinely being cancelled (re-raise — honor it).

    The E2B envd RPC layer converts a connect-level CANCELED — an HTTP/2 stream reset / a connection
    dropped mid-request under load — into ``asyncio.CancelledError`` (``e2b/envd/rpc.py``: "restore the
    original CancelledError"). On a poll read that is a blip: the sandbox keeps advancing and the value
    will be there next poll, so cutting the wait would ORPHAN a turn whose result the warm session is
    still writing (the room then hears nothing on a wake that actually SUCCEEDED server-side). A GENUINE
    task cancellation raises the same type at the same await point, so we disambiguate the only robust
    way: ``asyncio.current_task().cancelling()`` > 0 means a real cancellation is pending on THIS task
    (honor it); otherwise the ``CancelledError`` came from the E2B transport and this poll simply saw no
    data. Physics (a liveness poll tolerating a transport blip), not a situation→action rule (Law 4)."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() == 0


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
    #: HONEST DELIVERY (Law 2). True iff the agent intended to SPEAK but the LIVE relay POST for a
    #: spoken answer sentence FAILED inside the sandbox — the room never heard it, even though the
    #: turn otherwise succeeded (no ``error``, empty ``sent`` since the failure is a skipped
    #: ``relay_error`` line). The driver reads this to speak ONE honest degrade rather than treat the
    #: swallowed miss as a delivered success. False on a clean delivery, in file/proof mode (nothing
    #: to fail — the intents are replayed), and on any host record that omits the key (older records).
    delivery_failed: bool = False
    #: HOST-SIDE QUEUE LATENCY (ms) — how long this wake sat in the sandbox's single-flight WAKE_IN
    #: queue (behind a prior in-flight turn) before the warm host started serving it (BUG 5). ~0 for a
    #: wake that hit an idle host; large when it queued behind a long turn (the measured ~30s+ live
    #: gap). Surfaced so the live battery can assert the feed→turn-start gap. 0.0 = not measured.
    queued_ms: float = 0.0


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
    #: Whether the WARM session host launched successfully (at provision, or after a restart). Purely
    #: informational now — :meth:`run_ask` ALWAYS attempts the warm round-trip and self-heals via
    #: :meth:`_restart_session_host` on a miss; there is no separate cold engine to route to.
    warm: bool = False
    #: Latches True once the host's readiness breadcrumb is first seen — so only the FIRST wake pays
    #: the readiness wait; every later wake goes straight to the (warm) turn.
    _host_ready: bool = False
    #: Optional HOST directory the per-wake record (the DID trace) is MIRRORED into as it is read
    #: from the sandbox, so a host-side monitor (the live-test harness) can see the tools/cache-vs-
    #: read/timing/sent trace WITHOUT reaching into the sandbox. Empty ⇒ no mirror (production
    #: default — the record stays inside the isolated microVM). The mirror is a monitoring TAP, never
    #: on the meeting path: a mirror write fault is swallowed (the room is unaffected). When unset it
    #: defaults from ``PROXY_WAKE_OUT_MIRROR``, then ``PROXY_WAKE_OUT`` — the SAME dir the harness
    #: monitor reads — so setting ONE env var on the control-plane host wires the bridge end to end.
    #: (On the HOST process ``PROXY_WAKE_OUT`` is the mirror dir; the ``/tmp/wake_out`` sandbox path
    #: is a DIFFERENT process's env, set inside the microVM by ``_start_session_host`` — no clash.)
    wake_out_mirror: str = ""

    def __post_init__(self) -> None:
        if not self.wake_out_mirror:
            self.wake_out_mirror = (
                os.environ.get("PROXY_WAKE_OUT_MIRROR", "").strip()
                or os.environ.get("PROXY_WAKE_OUT", "").strip()
            )

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

    async def _read_file(self, path: str) -> str | None:
        """Read a seeded file back for boot-verification. Returns the content, or ``None`` if the
        file is absent (the real E2B SDK RAISES on an absent path — treated as "did not land")."""
        try:
            outcome = await self.call(lambda: self.sandbox.files.read(path), service="e2b")
        except Exception:  # noqa: BLE001 — an absent/failed read is "did not land" → verify fails
            return None
        val = getattr(outcome, "value", outcome)
        return None if val is None else str(val)

    async def _seed_and_verify(self, seed_files: list[tuple[str, str]]) -> None:
        """Write the ONE explicit seed set into the sandbox, then BOOT-VERIFY it — halting prep loudly
        (:class:`WorkroomSeedError`) if anything the resident CLAUDE.md depends on did not land.

        Two checks, so a meeting NEVER starts on a dangling resident import:
        1. Every seeded file is READ BACK non-empty (a write that silently didn't land — a template
           shadow, a transient fault — is caught here, not at the first wake).
        2. Every native ``@import`` in the composed CLAUDE.md resolves to a file in the seed set (so
           the interaction layer — the behavioral craft — is guaranteed present, never dangling).

        This is fail-closed by design (Law 1: grounded or silent): the provisioner catches the halt
        and boots the meeting WITHOUT a workroom rather than with a half-seeded one."""
        seeded_paths = {path for path, _ in seed_files}
        for path, content in seed_files:
            await self._write_file(path, content)
        # (1) read-back every seeded file — a write that didn't land is a halt, not a silent gap.
        missing = [path for path in (p for p, _ in seed_files) if not (await self._read_file(path))]
        if missing:
            raise WorkroomSeedError(
                f"workroom seed incomplete — these files did not land in the sandbox: {missing}"
            )
        # (2) every CLAUDE.md @import must resolve to a seeded file (no dangling resident import).
        claude_md = dict(seed_files)[PRIME_FILE]
        dangling = [imp for imp in _claude_md_imports(claude_md) if imp not in seeded_paths]
        if dangling:
            raise WorkroomSeedError(
                f"resident CLAUDE.md has @import(s) to unseeded file(s) (dangling): {dangling} — "
                f"the interaction layer / imported craft would never reach the agent"
            )

    async def feed_transcript(self, transcript_md: str) -> None:
        """Materialize the live transcript into the sandbox as a CRASH/RECONNECT RECOVERY record.

        This is NOT the primary recall path (SPEC §3): a woken turn recalls the meeting from the
        WARM session's resident cache — each wake inlines only the delta, which accumulates in-context
        — so it never reads this file just to know what was said. The file exists so that if the warm
        session is lost and restarted, or a reconnect needs to re-seed context, the room-so-far is on
        disk to catch up from. The bridge writes a bounded tail as lines accumulate. Never crashes the
        meeting on a write fault."""
        try:
            await self._write_file(TRANSCRIPT_FILE, transcript_md)
        except Exception:  # noqa: BLE001 — transcript sync never crashes the meeting
            logger.exception("workroom transcript sync failed (meeting continues)")

    async def read_transcript(self) -> str:
        """Read the sandbox ``MEETING_NOTES.md`` (the meeting transcript capture) as host-side text.

        The session continuously feeds the live transcript into the sandbox as ``MEETING_NOTES.md``
        (a crash/reconnect record). This host-side read surfaces that capture for MONITORING — the
        live-test HEARD tap confirms the transcript is actually being captured (even when Proxy stays
        silent). Best-effort: any read fault degrades to ``""`` (the monitor records the gap honestly),
        never a raise. Rides the ONE ``call_external`` seam like every E2B round-trip."""
        try:
            outcome = await self.call(
                lambda: self.sandbox.files.read(TRANSCRIPT_FILE), service="e2b"
            )
            return str(getattr(outcome, "value", outcome) or "")
        except Exception:  # noqa: BLE001 — a monitoring read fault is an honest "", never a crash
            logger.warning("workroom transcript read failed (monitoring only)", exc_info=True)
            return ""

    async def run_ask(self, ask: str, *, delta: str = "") -> WorkroomResult:
        """Wake Claude in the workroom on ONE reactive ask; return the result.

        ``delta`` is the NEW transcript since the last wake (SPEC §3). It is inlined into the wake
        prompt handed to the WARM session — where it enters the cached conversation — so the whole
        meeting ACCUMULATES resident turn-over-turn: the agent already knows everything said before,
        and only this delta + the ask are fresh this turn. Nothing directs it to read a transcript
        file for recall; ``MEETING_NOTES.md`` exists only as a crash/reconnect record.

        There is ONE delivery path — the WARM permanent session (``session_host.py``, started at
        provision): the prime + the ``to_meeting`` MCP tool are already loaded, so a wake is a fast
        file round-trip to an already-warm session. The wake prompt (the exact instructions below) is
        handed to that session; its result is parsed into a :class:`WorkroomResult`. If the warm host
        isn't up/responding (never started, crashed, or the round-trip times out) this RESTARTS the
        session host and RETRIES the warm turn ONCE; if it still can't be reached the turn honest-
        degrades to a :class:`WorkroomResult` error (§9) — never a crash. The turn reaches the room
        two ways (Design B): the agent's spoken prose is streamed to the voice channel, and its ONE
        ``to_meeting`` tool carries the non-spoken mediums (chat/dm/screen/offer/mute/unmute) — both
        relayed live to the host connection (or recorded locally when there is no relay).

        Never raises — a failed turn is an honest ``WorkroomResult.error`` (§9). This includes a
        TRANSPORT-induced ``CancelledError`` from a wake's own E2B I/O: the E2B/httpx/anyio stack
        cancels an in-flight read's cancel-scope when the HTTP/2 connection is reset / GOAWAYs under
        load, which surfaces here as a ``CancelledError`` even though the meeting is NOT ending. Left
        unhandled it would crash the meeting driver on a single flaky poll (WS6 long-session
        regression: 2 such cancels across ~26 wakes on real E2B). So a spurious transport cancel is
        absorbed into an honest ``WorkroomResult.error`` (the wake simply delivered nothing this turn —
        the room self-heals on the next line); ONLY a GENUINE task cancellation pending on THIS task
        (a real meeting-end drain — ``current_task().cancelling() > 0``) is re-raised so shutdown stays
        prompt (Law 3: human control / prompt teardown is never swallowed)."""
        prompt = self._wake_prompt(ask, delta)
        try:
            return await self._run_ask_once(ask, prompt)
        except asyncio.CancelledError:
            # A genuine caller drain (meeting end) increments this task's cancelling() count — honor
            # it. A cancelling()==0 CancelledError is a transport cancel-scope blip surfaced from the
            # E2B round-trip → absorb it as an honest no-reply turn (never crash the meeting loop).
            if not _poll_cancel_is_spurious():
                raise
            logger.warning("wake absorbed a transport-induced cancel — honest no-reply this turn",
                           exc_info=True)
            return WorkroomResult(ask=ask, error="workroom transport interrupted this turn")

    async def _run_ask_once(self, ask: str, prompt: str) -> WorkroomResult:
        """The warm turn + single self-heal, wrapped by :meth:`run_ask` (which absorbs a transport
        cancel). Returns the parsed result or an honest degrade — never fakes a reply."""
        warm = await self._run_ask_warm(ask, prompt)
        if warm is not None:
            return warm
        # The warm host missed (never opened, crashed, or the round-trip timed out). Restart it once
        # and retry — a single self-heal that recovers a faulted session without a whole second
        # engine to maintain. Still a bounded, honest attempt: if the restarted host doesn't answer
        # either, the turn degrades to an honest error rather than hanging or faking a reply.
        logger.warning("warm session miss for meeting ask — restarting the session host, retry once")
        if await self._restart_session_host():
            warm = await self._run_ask_warm(ask, prompt)
            if warm is not None:
                return warm
        logger.error("warm session unavailable after restart — honest degrade (no reply this turn)")
        return WorkroomResult(ask=ask, error="workroom session unavailable")

    @staticmethod
    def _wake_prompt(ask: str, delta: str = "") -> str:
        """The one wake prompt (judge-if-addressed → decline false wake → deliver in one turn →
        output-excellence → offer). Built once here so the warm session (and its restart-retry) hand
        Claude the SAME instructions on every wake — one behavior contract.

        Only the transcript DELTA since the last wake is inlined (SPEC §3). Because this runs on the
        WARM session, everything inlined on prior wakes is already in the cached conversation — so the
        agent ALREADY knows the whole meeting resident, and this turn only needs the new lines + the
        ask. There is deliberately NO direction to read a transcript file for recall: recall comes
        from the resident cache, not a per-wake file read (``MEETING_NOTES.md`` is only a crash record)."""
        delta_block = (
            "The latest transcript since you last checked in (you already know everything said before "
            f"this — it's resident in your memory of the room):\n{delta}\n\n"
            if delta.strip() else ""
        )
        return (
            "Someone in the room may have addressed you (your name was heard). Your identity and how "
            "to behave are in ./CLAUDE.md; you've been in this meeting the whole time and remember the "
            "conversation — no need to go re-read it anywhere.\n\n"
            f"{delta_block}"
            f"The line that woke you:\n{ask}\n\n"
            "FIRST judge whether you were genuinely being addressed — make this call FAST, from what "
            "you already know of the room, before you read any code or investigate anything. If 'proxy' was used "
            "incidentally (e.g. \"our proxy server\", \"the proxy pool\", a proxy/nginx config) or "
            "people are talking among "
            "themselves and no one is actually asking you for anything, STAY SILENT. Your words are "
            "spoken aloud, so a spoken \"not addressed\" / \"staying quiet\" / any reasoning is itself "
            "an unwanted interruption — the room must hear NOTHING from you. To stay silent, output "
            "NOTHING except this one exact line and nothing else:\n"
            "[SILENT]\n"
            "That single sentinel line is swallowed by the delivery layer and never reaches the room "
            "(if you must jot WHY you stayed quiet, put it only AFTER that line — it is never spoken). "
            "Don't call the tool, don't explore the repo. Silence is the correct, complete response to "
            "cross-talk. Only start real work — or say a single word aloud — once you're sure you were "
            "really addressed. ALSO stay [SILENT] if this line is just a REPEAT of something you ALREADY "
            "answered moments ago (people often re-ask the same thing seconds apart, or say it once "
            "casually and then again with your name — you can see in the recent transcript that you "
            "already gave this answer): answering the duplicate would talk over the room with the same "
            "thing twice. Only re-answer a repeat if they're clearly asking you to say it AGAIN.\n\n"
            "If you were addressed, do exactly as much as the ask needs — a greeting or a quick "
            "question is one direct reply with no tools; a real task gets the real work (read the "
            "ACTUAL code, run code to verify, draft real files). SPEAK by simply writing your reply — "
            "your words are spoken to the room live, streamed sentence by sentence as you type. Use "
            "your `mcp__meeting__to_meeting` tool (call it by that exact name — already loaded, don't "
            "search for it) ONLY for the non-spoken channels: chat/dm/screen/offer/mute/unmute. CRITICAL: "
            "deliver the real result IN THIS TURN — do the work now and hand over the actual "
            "result/artifact before you stop. To the ROOM this whole response is ONE continuous "
            "moment: even though you may take several internal steps to get there, speak as if you did "
            "it just now in one go — NEVER tell the room you \"already did this\" / did it in a "
            "\"previous turn\" / \"last turn\" / \"as I showed earlier\", never claim an artifact is "
            "\"already up\" unless you actually produced it this response, and never re-offer or "
            "restage as if repeating yourself. Your internal steps are not earlier exchanges with the "
            "room; unless the action really happened earlier in the meeting, it is happening NOW — narrate "
            "it that way (Law 2: never overstate what has happened). Never say \"I'll bring it back\" / "
            "\"give me a few "
            "minutes\" / \"will follow up\" and then stop with nothing delivered — that leaves the "
            "room hanging. The ONE exception: if you genuinely CANNOT finish without an answer from "
            "the room (a real ambiguity, or a blocker only they can clear), ask that ONE question "
            "clearly and stop — when they reply you WILL be brought back with their answer to "
            "continue this same task, so ask crisply rather than guessing (Law 1: grounded or "
            "silent). Otherwise do the ENTIRE task now — lead with the answer itself when it's close "
            "(don't spend your first line on \"on it…\" when the reply is a second away); save a "
            "brief \"give me a moment\" for genuine multi-step work that will visibly take a while, "
            "and then you MUST finish it. Before you stop you MUST have delivered "
            "the real result/artifact to the room in this same turn. For anything "
            "world-touching, produce the real artifact and, as your final step, offer it "
            "(medium='offer') — you have no push/send credentials by design, so the offer IS the "
            "delivery."
        )

    def _wake_envs(self) -> dict[str, str]:
        """The subscription auth + the relay wiring the in-sandbox MCP server needs. When ``relay_url``
        is empty the server appends intents to ``PROXY_MEETING_OUT`` (no live relay) — honest degrade."""
        envs = {
            "CLAUDE_CODE_OAUTH_TOKEN": self.token,
            "PROXY_MEETING_RELAY": self.relay_url,
            "PROXY_MEETING_TOKEN": self.relay_token,
            "PROXY_MEETING_OUT": TO_MEETING_OUT,
            # 1-HOUR PROMPT-CACHE TTL. The resident prime (behavioral prime + interaction layer +
            # codebase understanding + tool schemas) is the big cached prefix; on the default 5-min TTL
            # a quiet meeting stretch >5 min would expire it and the next wake would cold re-encode the
            # whole prefix (slow + costly). A meeting fits inside one hour, so the 1h TTL keeps the
            # cache warm across the entire session — quiet gaps no longer expire it (no keep-warm ping
            # needed). This is an env toggle honored by the Claude Code CLI (the SDK exposes no TTL
            # option); on subscription auth 1h may already be the default, so this only makes it explicit.
            "ENABLE_PROMPT_CACHING_1H": "1",
        }
        # Tool-workshop convenience: forward the Context7 key ONLY when the founder has provisioned it
        # (from Secret Manager into this host's env). Present ⇒ the warm session host pre-wires the
        # Context7 (live library docs) MCP; absent ⇒ nothing is added and behavior is unchanged. It is
        # gated because Context7 is egress-dependent (npx-fetch + a live API call per lookup) and
        # read-egress is the founder-gated infra toggle — see ``session_host.CONTEXT7_API_KEY``.
        context7_key = os.environ.get("CONTEXT7_API_KEY", "").strip()
        if context7_key:
            envs["CONTEXT7_API_KEY"] = context7_key
        # SERENA (symbol-level code intel) enablement: forward the opt-in signals ONLY when set
        # host-side, so a deployment can turn Serena on without a baked template (the baked template
        # instead sets these as image ENV). The warm session host gates on real availability
        # (``session_host._serena_server`` — shutil.which / PROXY_SERENA / PROXY_SERENA_CMD), so a
        # forwarded flag that can't actually run Serena still stands up NO child (Law 2).
        for serena_env in ("PROXY_SERENA", "PROXY_SERENA_CMD", "PROXY_SERENA_CONTEXT"):
            val = os.environ.get(serena_env, "").strip()
            if val:
                envs[serena_env] = val
        # Research-spend keys (founder-authorized): forwarded ONLY when the founder has provisioned
        # them host-side, so the agent can ACTUALLY run things it recommends (fire test renders,
        # compare image models) instead of only describing them — the go-above-and-beyond principle
        # made executable. These are read/generate spend keys, NOT push/send creds — the Law-3
        # credential boundary (no world-touching creds in the sandbox) is unchanged. Absent ⇒
        # nothing is added and the agent degrades honestly ("I'd need a key to run this").
        for research_key in ("FAL_KEY", "REPLICATE_API_TOKEN", "OPENAI_API_KEY"):
            val = os.environ.get(research_key, "").strip()
            if val:
                envs[research_key] = val
        return envs

    async def _await_host_ready(self, *, timeout: float | None = None) -> bool:
        """Wait (bounded) for the warm host's readiness breadcrumb, then latch ``_host_ready``.

        The persistent session takes a beat to open (the SDK client + the MCP stdio child + loading
        the prime). Called at PROVISION with a generous ``timeout`` to warm the host BEFORE the meeting
        goes live (so no wake races it); by a wake (default budget); and after a restart. A host that
        never comes up returns False so the wake restarts-and-retries, then degrades. NEVER raises."""
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
            except asyncio.CancelledError:
                # A spurious E2B transport cancel is just "not ready this poll"; a genuine task
                # cancellation (a pending request on THIS task) is re-raised to honor the caller.
                if not _poll_cancel_is_spurious():
                    raise
                raw = ""
            if raw:
                self._host_ready = True
                return True
            await asyncio.sleep(_WARM_POLL_S)
        return False

    async def _read_heartbeat(self) -> str:
        """The host's current heartbeat token — the content of HOST_READY_FILE, which the warm host
        REWRITES every beat (idle and mid-turn) with a fresh timestamp. A CHANGED value between reads
        ⇒ the host is alive (even deep in a long turn); a value frozen for the whole dead-host budget
        ⇒ the host is gone/hung. Returns "" on any read fault (treated as no-progress this poll — the
        budget, not a single miss, decides). NEVER raises."""
        try:
            return str(getattr(
                await self.call(lambda: self.sandbox.files.read(HOST_READY_FILE), service="e2b"),
                "value", "",
            ) or "")
        except Exception:  # noqa: BLE001 — a transient/absent read is just "no beat seen this poll"
            return ""
        except asyncio.CancelledError:
            # A spurious E2B transport cancel is "no beat this poll" (the dead-host BUDGET, not one
            # missed read, decides); a genuine task cancellation is re-raised to honor the caller.
            if not _poll_cancel_is_spurious():
                raise
            return ""

    async def _run_ask_warm(self, ask: str, prompt: str) -> WorkroomResult | None:
        """Serve the wake on the WARM permanent session (``session_host.py``): confirm the host is
        ready, append the wake to ``WAKE_IN`` with a fresh id, then poll ``WAKE_OUT/<id>.json``
        (bounded by ``ASK_TIMEOUT_S`` once the turn is running).

        Returns the parsed :class:`WorkroomResult` on success, or ``None`` on ANY miss (host never
        came up, the round-trip timed out, a write/read fault) — a ``None`` tells :meth:`run_ask` to
        restart the host and retry once. NEVER raises. If the host wrote an honest per-turn ``error``
        we still surface it as a real result (that IS the answer — the session degrades, no retry)."""
        if not await self._await_host_ready():
            return None  # the warm host never opened → restart-and-retry (fast)
        wake_id = uuid.uuid4().hex
        # Stamp the enqueue wall-time so the host can measure QUEUE LATENCY (BUG 5): the warm session
        # is single-flight (one ClaudeSDKClient), so a wake enqueued behind an in-flight turn waits;
        # ``queued_at`` → the host computes ``queued_ms`` = how long it sat before serving.
        req = json.dumps({"id": wake_id, "prompt": prompt, "queued_at": time.time()})
        try:
            # Append the wake line (the host tails WAKE_IN). ``printf '%s\n' <arg>`` writes the JSON
            # verbatim + one newline; ``shlex.quote`` safely quotes the payload's embedded quotes so
            # exactly one valid JSON line lands (no partial/concatenated line the host could mis-read).
            await self._run(
                f"mkdir -p {shlex.quote(WAKE_OUT)} && "
                f"printf '%s\\n' {shlex.quote(req)} >> {shlex.quote(WAKE_IN)}",
                timeout=30.0,
            )
        except Exception:  # noqa: BLE001 — a write fault → restart-and-retry (never crash)
            logger.exception("warm wake enqueue failed — will restart the session host and retry")
            return None
        out_path = f"{WAKE_OUT}/{wake_id}.json"
        deadline = time.monotonic() + ASK_TIMEOUT_S
        start = time.monotonic()
        # DEAD-HOST watch: the wake is only allowed the full ASK_TIMEOUT_S while the host is LIVING —
        # proven by its heartbeat breadcrumb advancing. If the breadcrumb value hasn't changed for the
        # whole _DEAD_HOST_TIMEOUT_S window (host OOM'd/SIGKILLed/hung mid-turn) we STOP waiting and
        # tell run_ask to restart-and-retry — seconds, not the 900s ceiling (no 15-min dead air on a
        # crashed host). A long WORKING turn keeps the heartbeat moving, so it is never cut short.
        last_beat = await self._read_heartbeat()
        last_beat_change = time.monotonic()
        while time.monotonic() < deadline:
            try:
                raw = getattr(
                    await self.call(lambda: self.sandbox.files.read(out_path), service="e2b"),
                    "value", "",
                )
            except Exception:  # noqa: BLE001 — not written yet (or a transient read) → keep polling
                raw = ""
            except asyncio.CancelledError:
                # The result read is the CRITICAL one: the E2B SDK converts a connect-level CANCELED
                # (HTTP/2 reset / dropped connection under load) into CancelledError, and letting that
                # kill the poll would ABANDON a wake whose result the warm session is still writing —
                # the room hears nothing on a turn that SUCCEEDED in the sandbox. So a spurious
                # transport cancel is just "not written yet this poll" (keep polling); only a genuine
                # task cancellation (meeting-end drain) is re-raised.
                if not _poll_cancel_is_spurious():
                    raise
                raw = ""
            if raw:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    return None  # a corrupt result → restart-and-retry (never present garbage)
                # Monitoring TAP (never on the meeting path): mirror the raw per-wake record to the
                # host dir so the live-test harness monitor can read the DID trace without reaching
                # into the sandbox. A mirror fault is swallowed — the room's turn is unaffected.
                self._mirror_wake_record(wake_id, raw)
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
                    delivery_failed=bool(rec.get("delivery_failed", False)),
                    queued_ms=float(rec.get("queued_ms", 0.0) or 0.0),
                )
            # No result yet — is the host still ALIVE? Check its heartbeat: a changed value means it is
            # (idle or grinding on a long turn); if it hasn't moved for the whole dead-host window the
            # host is gone/hung → stop fast so run_ask restarts-and-retries (vs. spinning to 900s).
            beat = await self._read_heartbeat()
            now = time.monotonic()
            if beat and beat != last_beat:
                last_beat, last_beat_change = beat, now
            elif now - last_beat_change >= _DEAD_HOST_TIMEOUT_S:
                logger.error(
                    "warm host heartbeat frozen %.0fs (no result) — host presumed dead, aborting the "
                    "wait to restart-and-retry (not the %.0fs ceiling)",
                    now - last_beat_change, ASK_TIMEOUT_S,
                )
                return None  # dead/hung host → restart-and-retry FAST (blocker B2 fix)
            # Adaptive backoff: poll fast at first so a quick reply lands snappily, then EASE OFF so a
            # multi-minute task (PRD, deep code) doesn't hammer the E2B files API hundreds of times
            # (~720 reads over 3 min at a flat 0.25s → E2B contention/cancellation under load). This
            # cuts the long-task read count ~8x while keeping short-wake latency low.
            elapsed = time.monotonic() - start
            # Tighter backoff than before (was 1.0/2.0) so a finished turn is DETECTED fast — the
            # poll lag is pure latency after Claude is done. Still eases off on long tasks to avoid
            # hammering the E2B files API: 0.25s snappy start, 0.5s mid, 1.0s for multi-minute work.
            await asyncio.sleep(_WARM_POLL_S if elapsed < 5.0 else (0.5 if elapsed < 30.0 else 1.0))
        return None  # timed out waiting on the warm host → restart-and-retry

    def _mirror_wake_record(self, wake_id: str, raw: str) -> None:
        """Mirror one per-wake record's RAW JSON into ``wake_out_mirror`` (the host monitor dir).

        The record is written INSIDE the sandbox (``$PROXY_WAKE_OUT/<id>.json``); the harness monitor
        reads a HOST directory. This copies the record the host just read out of the sandbox onto the
        host so the DID trace (tools / cache-vs-read / timing / sent) reaches the monitor. A no-mirror
        deployment (``wake_out_mirror`` empty) is a no-op. Written atomically (temp + rename) so the
        monitor never sees a half-file, and NEVER-throw: a mirror fault is logged, never raised — this
        is a monitoring tap, not the meeting path."""
        mirror = self.wake_out_mirror
        if not mirror:
            return
        try:
            dest_dir = pathlib.Path(mirror)
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp = dest_dir / f".{wake_id}.json.tmp"
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(dest_dir / f"{wake_id}.json")
        except Exception:  # noqa: BLE001 — a monitoring-tap fault never affects the meeting turn
            logger.warning("wake-record mirror to %s failed (monitoring only)", mirror, exc_info=True)

    async def _restart_session_host(self) -> bool:
        """Restart the WARM session host after a miss, then wait (bounded) for it to come READY —
        the single self-heal :meth:`run_ask` uses instead of a whole second (cold) engine.

        Kills any lingering host process, clears the latched readiness so the next wait re-checks the
        fresh breadcrumb, relaunches the host (:func:`_start_session_host`), and awaits readiness on
        the normal budget. Returns True iff the restarted host opened (so the caller retries the warm
        turn); False degrades the turn honestly. NEVER raises — a restart fault is just a False."""
        try:
            # Best-effort: stop a half-dead prior host so the fresh one owns WAKE_IN/WAKE_OUT cleanly.
            # ``|| true`` keeps a no-match pkill from failing the command (nothing to kill is fine).
            await self._run(f"pkill -f {shlex.quote(SESSION_HOST_FILE)} || true", timeout=30.0)
        except Exception:  # noqa: BLE001 — the kill is best-effort; relaunch regardless
            logger.warning("session-host kill before restart failed (continuing)", exc_info=True)
        self._host_ready = False
        self.warm = await _start_session_host(self)
        if not self.warm:
            return False
        return await self._await_host_ready()

    async def pause(self) -> str | None:
        """Pause the sandbox at teardown so a warm per-repo snapshot can RESUME in ~1s (vs a cold
        clone+install). Returns the paused sandbox id on success, or ``None`` when pause is disabled /
        unavailable / failed (then the caller simply tears down cold). NEVER raises — pause is an
        optimization behind the ``PROXY_ENABLE_PAUSE_RESUME`` flag.

        FLAG-GATED (default OFF): with the flag off this is a no-op ``None`` so teardown is the normal
        cold kill — the working path is unchanged (E2B persistence is public beta; see the flag note).

        SDK method: the ASYNC E2B SDK exposes persistence as ``beta_pause()`` (verified against the
        v2.x async SDK reference), NOT ``pause()`` — so we prefer ``beta_pause`` and fall back to a
        plain ``pause`` only if a future/sync SDK renames it. Resume is wired in
        :func:`provision_workroom` via ``resume_id`` (``AsyncSandbox.connect(id)`` auto-resumes)."""
        if not _pause_resume_enabled():
            return None  # flag off → tear down cold (working path unchanged)
        pause = getattr(self.sandbox, "beta_pause", None) or getattr(self.sandbox, "pause", None)
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


class WorkroomSeedError(RuntimeError):
    """Raised when the ONE boot-verified seed set does NOT fully land in the sandbox — a seeded file
    is missing after write, or a native ``@import`` in the composed CLAUDE.md points at a file that
    was not seeded (a DANGLING resident import). Halting prep loudly here is the correct fail-closed
    behavior: the provisioner catches it and boots the meeting WITHOUT a workroom (honest degrade),
    rather than letting a meeting start on a resident prime whose behavioral core never loads."""


_IMPORT_LINE = re.compile(r"(?m)^\s*@([\w./\-]+)\s*$")


def _claude_md_imports(claude_md: str, *, repo_dir: str = REPO_DIR) -> list[str]:
    """The sandbox paths every native ``@import`` in the composed CLAUDE.md resolves to.

    Claude Code resolves ``@path`` imports in CLAUDE.md RELATIVE to the file, so ``@./FOO.md`` (or
    ``@FOO.md``) loads ``<repo_dir>/FOO.md`` seeded beside CLAUDE.md. We only treat a line that is
    JUST an ``@path`` token as an import (the shape ``compose_resident_prime`` emits) — never an
    ``@`` that happens to appear mid-prose — so the boot check can't false-positive on the prime."""
    out: list[str] = []
    for rel in _IMPORT_LINE.findall(claude_md):
        out.append(f"{repo_dir}/{rel.lstrip('./')}")
    return out


def _packaged_source(name: str) -> str:
    """A packaged script's source, read off disk next to this module — the ONE source of truth for a
    file COPIED into the sandbox verbatim at provision (the script runs where the workspace is not
    installed, so it can't be imported there)."""
    return (pathlib.Path(__file__).with_name(name)).read_text(encoding="utf-8")


def _packaged_skill_source(name: str) -> str:
    """A packaged skill's ``SKILL.md``, read off disk from ``skills/<name>/SKILL.md`` beside this
    module — the source of truth for a skill COPIED into the sandbox at provision. Skills live in a
    subdir (not a sibling file), so this can't reuse :func:`_packaged_source`."""
    return (pathlib.Path(__file__).parent / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def _mcp_server_source() -> str:
    """The packaged in-sandbox MCP server source (``sandbox_meeting_mcp.py``)."""
    return _packaged_source("sandbox_meeting_mcp.py")


def _session_host_source() -> str:
    """The packaged WARM session-host source (``session_host.py``) — the ONE persistent Claude
    session that serves every wake (no per-turn spawn)."""
    return _packaged_source("session_host.py")


def _seed_files(*, prime: str, map_text: str, meeting_info: str) -> list[tuple[str, str]]:
    """The ONE explicit seed set: every file the resident CLAUDE.md / ``.mcp.json`` depends on, as
    ``(sandbox_path, content)`` pairs written verbatim into the sandbox at provision.

    This is the single source of truth for "what the warm session needs on disk" — so nothing named
    in CLAUDE.md (the behavioral prime, its ``@import`` of the interaction layer, the roster it reads,
    the MCP server + ``.mcp.json`` that give it its one connection to the room, the session host that
    serves each wake) can be silently missing. :func:`provision_workroom` writes THIS list and then
    boot-verifies it (:meth:`Workroom._seed_and_verify`); any gap HALTS prep (:class:`WorkroomSeedError`).

    - **CLAUDE.md** — the lean behavioral prime + the resident understanding, composed with the native
      ``@./INTERACTION_LAYER.md`` import (:func:`compose_resident_prime`).
    - **INTERACTION_LAYER.md** — the packaged craft (``interaction_layer.md``) that CLAUDE.md imports;
      the T4 fix — before this it was NEVER seeded, so the import dangled and the craft never loaded.
    - **MEETING_INFO.md** — the roster (who's in the room); an honest placeholder when none is known.
    - **REPO_MAP.md** — the map as an older-context fallback (the resident block in CLAUDE.md is primary).
    - **MEETING_NOTES.md** — the empty transcript/recovery record the bridge appends to.
    - **sandbox_meeting_mcp.py** + **.mcp.json** — the in-sandbox MCP server (the one ``to_meeting``
      connection) and the stdio config that registers it with native ``claude`` (``alwaysLoad``).
    - **session_host.py** — the warm permanent session that serves every wake.

    - **.claude/skills/<name>/SKILL.md** — the three meeting skills (``meeting-artifact``,
      ``meeting-diagram``, ``background-job``), seeded to the CLI's discovery path so native ``claude``
      loads each ON-DEMAND when doing that kind of work. Progressive disclosure: they cost the always-on
      prime nothing, but are present the instant the interaction layer reaches for them (the layer names
      them by their skill name), so "put it on screen / build the diagram / run it in the background" is
      a real capability, not a dangling reference."""
    mcp_config = {
        "mcpServers": {
            "meeting": {"command": "python3", "args": [MCP_SERVER_FILE], "alwaysLoad": True}
        }
    }
    seeds = [
        (PRIME_FILE, compose_resident_prime(prime, map_text)),
        (INTERACTION_LAYER_FILE, _packaged_source(INTERACTION_LAYER_NAME)),
        (MEETING_INFO_FILE, meeting_info.strip() and meeting_info or _MEETING_INFO_PLACEHOLDER),
        (MAP_FILE, map_text or "(no pre-built map — explore the repo directly)"),
        (TRANSCRIPT_FILE, "# Meeting transcript\n"),
        (MCP_SERVER_FILE, _mcp_server_source()),
        (MCP_CONFIG_FILE, json.dumps(mcp_config)),
        (SESSION_HOST_FILE, _session_host_source()),
    ]
    # THE SKILLS (the interaction-layer unlock): seed each packaged skill to its CLI discovery path so
    # the layer's references to them ("the ``meeting-artifact`` skill", ``meeting-diagram``,
    # ``background-job``) resolve to a real, loadable skill instead of a dangling name.
    seeds += [
        (f"{SKILLS_DIR}/{name}/SKILL.md", _packaged_skill_source(name)) for name in SKILL_NAMES
    ]
    return seeds


async def _start_session_host(wr: Workroom) -> bool:
    """Start the WARM permanent session host as a BACKGROUND process in the sandbox, warm before the
    first wake. Also the relaunch step of :meth:`Workroom._restart_session_host`. Best-effort: on any
    fault we leave ``warm=False`` and the wake self-heals (restart-and-retry) or honest-degrades.

    The host is detached (survives the provision command's return), its stdout/stderr land in
    ``SESSION_HOST_LOG`` (a fault breadcrumb), and it inherits the subscription auth + the relay
    wiring so a warm turn's ``to_meeting`` reaches the host. Returns True iff the launch command ran
    (not that the session is fully open — a wake detects a dead host by timing out and restarting)."""
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
    except Exception:  # noqa: BLE001 — a launch fault leaves warm=False; the wake self-heals/degrades
        logger.warning("warm session host launch failed — wake will restart-and-retry", exc_info=True)
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
    meeting_info: str = "",
    prime: str = WORKROOM_PRIME,
    template: str | None = DEFAULT_TEMPLATE,
    sandbox_class: Any = None,
    relay_url: str = "",
    relay_token: str = "",
    resume_id: str | None = None,
    wake_out_mirror: str = "",
) -> Workroom:
    """Provision + seed a per-meeting workroom, warm and ready before the first ask.

    Provisions an E2B sandbox (pre-baked ``template`` when available), clones the repo in
    (shallow), ensures native ``claude`` is present, and seeds CLAUDE.md — the lean behavioral prime
    PLUS the RESIDENT codebase-understanding block (the ranked symbol map with real file:line,
    :func:`compose_resident_prime`), so the map rides the cached prime every wake with zero reads —
    the map (REPO_MAP.md) is also written as an older-context fallback, and an empty transcript file.
    It ALSO wires the agent's ONE meeting interface:
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
    # Resume is the pause/resume fast-join lever — GATED OFF by default (``PROXY_ENABLE_PAUSE_RESUME``).
    # With the flag off, ANY ``resume_id`` is ignored and we always cold-provision, so the working path
    # is unchanged (E2B persistence is public beta; needs live multi-cycle validation — see the flag).
    if resume_id and _pause_resume_enabled():
        # Fast join: reconnect to a paused warm snapshot (repo + deps already baked in). ``connect``
        # is the async classmethod that AUTO-RESUMES a paused sandbox (verified). A resume fault
        # degrades to a fresh provision below — never a crash, never a stuck meeting.
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
                  relay_url=relay_url, relay_token=relay_token,
                  wake_out_mirror=wake_out_mirror)

    # Setup (idempotent; a pre-baked template / resumed snapshot makes most of this a no-op/instant):
    #  - shallow-clone the repo into REPO_DIR (fast; ~6s on cal.com)
    #  - install the pinned mcp SDK + the claude-agent-sdk the WARM session host needs  (~11s cold)
    #  - ensure native claude is installed  (~7s cold)
    #
    # SERIALIZE THE TWO HEAVYWEIGHT INSTALLS (reliability over a few off-path seconds — measured on
    # real infra). These were run CONCURRENTLY to shave ~6-7s off the dependency phase, but the E2B
    # BASE sandbox has only ~478 MB RAM (measured: ``free -m`` → total 478), and running ``pip install``
    # (mcp + claude-agent-sdk) and ``npm install -g claude-code`` AT THE SAME TIME pushes peak memory
    # past that ceiling — the kernel OOM-kills one of them (observed: ``Killed`` on pip, then on npm),
    # which fails the WHOLE provision → NO meeting at all. Run serially and each fits comfortably
    # (proven: serial pip → OK, then serial npm → OK, ``claude --version`` runs). Provision happens at
    # JOIN, before anyone addresses Proxy, so those ~6s are entirely off the room's perceived critical
    # path — trading them for a provision that doesn't OOM is the right call every time (a pre-baked
    # template, the founder-gated deploy artifact, removes this cost entirely). pip FIRST (it is the
    # heavier peak); ``&&`` fails the setup on either install error (no silent half-provision).
    # (The npm EBADENGINE warning — claude-code wants node >=22, the base ships node v20 — is only a
    # WARNING: the CLI installs and runs fine on v20; the baked template will ship node >=22.)
    depth = "--depth 1" if sha is None else ""
    setup = (
        f"mkdir -p {shlex.quote(WORKROOM_ROOT)} && "
        f"(test -d {shlex.quote(REPO_DIR)}/.git || git clone {depth} {shlex.quote(repo_url)} "
        f"{shlex.quote(REPO_DIR)}) && "
        f"pip3 install -q {shlex.quote(MCP_PIN)} {shlex.quote(SDK_PIN)} && "
        f"{{ command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code; }}"
    )
    await wr._run(setup, timeout=PROVISION_TIMEOUT_S)  # noqa: SLF001 — same module
    if sha:
        await wr._run(f"cd {shlex.quote(REPO_DIR)} && git checkout -q {shlex.quote(sha)} || true",
                      timeout=120.0)  # noqa: SLF001
    # Seed the ONE explicit, boot-verified seed set — every file the resident CLAUDE.md / ``.mcp.json``
    # depends on, written together and then READ BACK + ``@import``-checked so nothing named in
    # CLAUDE.md can be silently missing (T4). This folds together what used to be scattered writes:
    #  - CLAUDE.md = the lean behavioral prime + the RESIDENT codebase-understanding block (composed by
    #    :func:`compose_resident_prime`, cached ONCE into the warm session's prefix → zero-read recall);
    #  - INTERACTION_LAYER.md = the packaged craft CLAUDE.md ``@import``s (THE T4 fix — it was never
    #    seeded before, so that import dangled and the behavioral core never reached the agent);
    #  - MEETING_INFO.md = the roster (folded in from the provisioner's former best-effort write);
    #  - REPO_MAP.md (older-context fallback), MEETING_NOTES.md (empty recovery record);
    #  - sandbox_meeting_mcp.py + .mcp.json (the ONE ``to_meeting`` connection, ``alwaysLoad`` so the
    #    sole line to the room is never deferred behind a ToolSearch) + session_host.py (the warm session).
    # A gap HALTS prep loudly (:class:`WorkroomSeedError`) — fail-closed, so a meeting never starts on a
    # dangling resident import; the provisioner catches it and boots without a workroom (honest degrade).
    await wr._seed_and_verify(  # noqa: SLF001 — same module
        _seed_files(prime=prime, map_text=map_text, meeting_info=meeting_info)
    )
    # RESUMED-SNAPSHOT HYGIENE: ``beta_pause`` restores RUNNING processes, so a resumed sandbox still
    # carries the OLD session host (and its stale SDK client / MCP stdio child) from before the pause.
    # Kill it before starting a fresh host, else two hosts would tail the same WAKE_IN and double-serve
    # every wake (double delivery to the room). Starting a FRESH host is safe: it re-sends the identical
    # resident prime, which still hits the Anthropic prompt cache (1h TTL) as a cache_read if within the
    # window — so we get clean single-host ownership without losing the cache benefit. Best-effort.
    if resumed:
        try:
            await wr._run(f"pkill -f {shlex.quote(SESSION_HOST_FILE)} || true", timeout=30.0)  # noqa: SLF001
        except Exception:  # noqa: BLE001 — best-effort cleanup; the fresh host is started regardless
            logger.warning("resumed-snapshot session-host cleanup failed (continuing)", exc_info=True)
    # START the WARM permanent session (the ONE delivery path): one persistent Claude session per
    # meeting, warm before the first wake. On any launch fault ``warm`` stays False and the first wake
    # self-heals (restart-and-retry) or honest-degrades.
    wr.warm = await _start_session_host(wr)
    if wr.warm:
        # Warm the host DURING provision (before anyone addresses Proxy) so the first wake finds it
        # ready — otherwise it races the host's ~15-30s SDK-client+prime open and the first wake pays
        # the readiness wait (then restarts-and-retries if still not up). This moves the warm-up off
        # the first response's critical path. Best-effort: not-ready-in-budget just defers to the wake.
        ready = await wr._await_host_ready(timeout=_WARM_PROVISION_WAIT_S)  # noqa: SLF001
        logger.info("warm host readiness at provision: sandbox=%s ready=%s", wr.sandbox_id, ready)
    logger.info("workroom provisioned: sandbox=%s repo=%s relay=%s warm=%s resumed=%s",
                wr.sandbox_id, repo_url, bool(relay_url), wr.warm, resumed)
    return wr
