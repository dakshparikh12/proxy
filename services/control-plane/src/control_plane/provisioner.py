"""``provisioner`` — the per-meeting runtime entry-point (04 §3.2/§3.6, CANONICAL §12.1).

This is the entry-point that turns the built pieces into a RUNNING meeting. On a
Recall ``in_call`` webhook it:

1. **Atomically claims** the meeting via ``ops.claim_meeting`` — an INSERT ... ON
   CONFLICT DO NOTHING against the ``operation_runs`` partial-unique index
   (``(scope_id, operation_type) WHERE status='running'``). The non-null returner
   owns the meeting; a concurrent duplicate join gets ``None`` and backs off — **one
   harness per meeting, no broker, no Redis** (§3.6). The winner's instance-id is
   written onto ``created_by`` (affinity, §3.6/§11.11).

2. **Assembles the subsystems in ONE scope** — transport (the ``SignalCarrier``),
   the Scribe runtime, the NEW in-meeting engine, and the abort seam — on a single
   :class:`~control_plane.meeting_runtime.MeetingRuntime`, and **binds the claimed row's
   fencing handle** onto the runtime so every side-effect reads ``is_owner`` live
   (§3.7 fencing). The ``SignalCarrier`` is subscribed **ONCE at join** (the Scribe
   consumer + the meeting-end listener share the one carrier); it is never re-wired
   per event.

3. **Launches the meeting-end spine** — :func:`run_meeting_until_end` is the
   ``asyncio.run``-style entry: it pumps the runtime's meeting-end listener and runs
   until the explicit ``MeetingEnd`` signal closes the carrier. A meeting has NO time
   cap (SPEC §9); the only wall clock is the generous env-configurable safety ceiling
   (``MEETING_MAX_HOURS``, default 12h) so a loop whose end signal never arrives can't
   leak an instance forever.

4. **Survives a recycle** — when the owning instance dies its heartbeat goes stale, the
   reaper (§3.8) flips the row off ``running``, the partial index frees, and a
   REPLACEMENT provisioner handed the same webhook **re-claims** the meeting. It then
   **confirms the transcript plane is reachable** so the first wake after the swap
   replays from it (restart-not-resume, §3.10): the media session cannot be resumed,
   but Proxy's judgment history is rebuilt from Doc 03's transcript plane on that
   first wake so the room stays coherent.

The provisioner does NOT redefine the claim, the assembly, or the resume fallback —
it is the thin entry that wires those built pieces into a live meeting.

THE CUTOVER (this node): the brain seat on the boot path is the NEW in-meeting engine
(``in_meeting.runtime.assemble_engine`` — map + code + meeting + sandbox access, the
Cartesia→Output-Media speak pipe, the real async disambiguator), assembled per meeting in
:func:`_assemble_engine` and stashed on the runtime (``runtime.engine``) so the webhook
drain feeds it transcript/chat by meeting id. The OLD live brain is DELETED; the only
carrier-side spine left here is the runtime's meeting-end listener (zero wake turns).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libs.db import Database, repos
from libs.ops import MEETING_HARNESS_OP, OperationHandle, claim_meeting

_log = logging.getLogger(__name__)

# A meeting has NO time cap (SPEC §9): the launched loop runs until the explicit
# ``MeetingEnd`` signal (§3.1), never a wall-clock kill. The ONLY remaining wall clock
# is a GENEROUS safety ceiling so a wedged loop can never leak an instance forever —
# env-configurable via ``MEETING_MAX_HOURS`` and resolved at LAUNCH time (never frozen
# at import), defaulting to 12 hours. The old 3600s hard cap force-closed every real
# meeting at 60 minutes and is deleted.
MEETING_MAX_HOURS_ENV = "MEETING_MAX_HOURS"
DEFAULT_MEETING_MAX_HOURS: float = 12.0

# Interval between sandbox keep-warm beats (IMP-3): the warm E2B sandbox is provisioned
# with ``SANDBOX_TIMEOUT_S`` (1h) and self-times-out; with no meeting cap, a live
# meeting can outlast it, so the provisioner extends the sandbox lifetime on this
# cadence while the meeting runs. Comfortably inside the 1h sandbox lifetime so a
# missed/failed beat still leaves a full beat before expiry. unit: seconds.
SANDBOX_KEEPWARM_INTERVAL_S: float = 1800.0


def _meeting_max_s() -> float:
    """The wall-clock SAFETY CEILING on one meeting's run loop, in seconds.

    NOT a meeting time cap (SPEC §9 — the meeting ends on the explicit ``MeetingEnd``
    signal): this is the leak backstop for a loop whose end signal never arrives (a
    dead carrier, a wedged listener). ``MEETING_MAX_HOURS`` overrides; an unset,
    unparsable, or non-positive value falls back to the generous 12h default —
    never unbounded-by-typo, never the old hard-coded hour.
    """
    raw = os.environ.get(MEETING_MAX_HOURS_ENV, "")
    try:
        hours = float(raw)
    except ValueError:
        hours = DEFAULT_MEETING_MAX_HOURS
    if hours <= 0:
        hours = DEFAULT_MEETING_MAX_HOURS
    return hours * 3600.0

# Bound on EACH meeting-end engine teardown step (drain the in-flight turns, flush +
# close the speak pipe, kill the warm sandbox). Teardown must never deadlock meeting
# end (§3.8): a hung turn, a stuck synth, or a hanging E2B kill is abandoned after
# this bound and the close still completes the operation row. unit: seconds.
ENGINE_TEARDOWN_TIMEOUT_S: float = 30.0

# Recall bot-status event names that mean "the bot is now IN the room" — the moment the
# harness claims + provisions the per-meeting runtime (mirrors ``control_plane.webhooks``).
_IN_CALL_EVENTS = frozenset(
    {"bot.in_call", "in_call", "bot.in_call_recording", "bot.joining_call"}
)


@dataclass
class ProvisionOutcome:
    """The result of handing an ``in_call`` webhook to the provisioner.

    ``claimed`` is the load-bearing bit: True iff THIS instance won the atomic claim and
    opened the harness; False iff a concurrent/existing harness already owns the meeting
    (this instance backed off). ``resumed`` records that a re-claim confirmed Doc 03's
    transcript plane is reachable for the first-wake §3.5 replay. ``ran_to_end`` is set by
    :func:`run_meeting_until_end` when the loop ran to the meeting-end signal (vs a timeout).
    """

    claimed: bool
    run_id: Any = None
    resumed: bool = False
    ran_to_end: bool = False


def _event_name(payload: dict[str, Any]) -> str:
    name = payload.get("event") or payload.get("type") or ""
    return str(name).strip().lower()


def _bot_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("bot_id"):
        return str(data["bot_id"])
    if payload.get("bot_id"):
        return str(payload["bot_id"])
    return None


async def provision_meeting(
    payload: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    resume: bool = False,
    history_fn: Any = None,
    provider: Any = None,
    transport: Any = None,
    speak: Any = None,
    disambiguate: Any = None,
    sandbox_backend: Any = None,
    model: str | None = None,
) -> ProvisionOutcome:
    """Claim + assemble the per-meeting harness from a Recall ``in_call`` webhook.

    The atomic-claim entry (§3.6): resolve the bot back to its meeting, then INSERT the
    ``meeting-harness`` ``operation_runs`` row via ``ops.claim_meeting``. On a WIN this
    instance owns the meeting — it assembles the runtime (transport carrier + Scribe +
    engine + abort in ONE scope), binds the claimed row's fencing handle so ``is_owner``
    gates every emit, and subscribes the carrier ONCE at join. On a LOSS (a concurrent
    duplicate join, or an already-running harness) it returns ``claimed=False`` and opens
    NO second runtime — one harness per meeting.

    ``resume=True`` marks a REPLACEMENT re-claim after a recycle: the win confirms Doc 03's
    transcript plane is reachable so the first wake after the swap replays from it via the
    §3.5 ``resume_with_fallback`` seam (which fires in :meth:`WakeTurn.run`, not here),
    keeping the room coherent across the instance swap. A non-``in_call`` event, or an
    unresolvable bot, is a safe no-op (``claimed=False``) — never a raise on the webhook path.

    THE CUTOVER: on a WON claim this is also where the NEW in-meeting engine is assembled
    (:func:`_assemble_engine` — map + code + meeting + sandbox access) and stashed on the
    runtime (``runtime.engine``) so the webhook drain feeds it by meeting id. The injection
    kwargs (``provider``/``transport``/``speak``/``disambiguate``/``sandbox_backend``/
    ``model``) are the engine's vendor seams — ``None`` everywhere means the REAL production
    edges (Claude provider, RecallTransport, Cartesia speak pipe, Haiku confirm, real E2B).
    """
    if _event_name(payload) not in _IN_CALL_EVENTS:
        return ProvisionOutcome(claimed=False)

    bot_id = _bot_id(payload)
    if bot_id is None:
        return ProvisionOutcome(claimed=False)

    async with db.acquire() as conn:
        resolved = await repos.meetings.get_by_bot_id(conn, bot_id)
    if resolved is None:
        return ProvisionOutcome(claimed=False)  # unknown bot never opens a harness
    meeting_id = str(resolved["id"])

    # An already-assembled runtime on THIS instance means we already own the meeting; a
    # redelivered in_call must NOT re-claim or re-wire (idempotent, subscribe-once).
    if registry.get(meeting_id) is not None:
        return ProvisionOutcome(claimed=False)

    # THE atomic claim (§3.6): the partial-unique index arbitrates the race. A non-null
    # id → we won and own the meeting; None → a concurrent/existing harness owns it.
    run_id = await claim_meeting(
        db, meeting_id, MEETING_HARNESS_OP, created_by=db.instance_id
    )
    if run_id is None:
        return ProvisionOutcome(claimed=False)  # lost the race — back off, no harness

    # WON: assemble the four subsystems in ONE scope on the meeting's runtime, binding the
    # claimed row's fencing handle so every emit reads is_owner live (§3.7).
    handle = OperationHandle(db, run_id, meeting_id, MEETING_HARNESS_OP)
    resumed = False
    if resume:
        # A replacement re-claim after a recycle: confirm Doc 03's transcript plane is
        # reachable before the loop runs, so the first wake replays Proxy's context from
        # it via the §3.5 seam (fired in WakeTurn.run) and the room stays coherent across
        # the instance swap (restart-not-resume, §3.10).
        resumed = await _resume_session(db, meeting_id, history_fn=history_fn)

    # Resolve THIS meeting's code_intel grounding (the tenant's durable graph.db + pinned
    # clone) from the SAME repo row the referent corpus uses (repo_id -> tenant + name) — the
    # async chokepoint where a db read is allowed (the sync ``_assemble_runtime`` cannot).
    # Fail-closed to None (an unindexed/unknown repo mounts no code_intel server; Proxy still
    # wakes) — never a raise on the join path (§3.8 / Rule 6).
    code_intel_ctx = None
    map_text = None
    try:
        from .code_intel_mount import (
            resolve_code_intel_context_from_row,
            resolve_map_text_from_row,
        )

        code_intel_ctx = await resolve_code_intel_context_from_row(resolved, db=db)
        # Additively resolve the pre-meeting MAP so the wake turn primes on it (the pre-meeting
        # system's downstream contribution). Independent of the code_intel ctx — an unmapped
        # repo simply yields None and the wake turn is unaffected.
        map_text = await resolve_map_text_from_row(resolved, db=db)
    except Exception:  # noqa: BLE001 - a resolution fault degrades to no code_intel, never blocks join
        code_intel_ctx = None

    runtime = _assemble_runtime(
        payload,
        resolved,
        db=db,
        registry=registry,
        handle=handle,
        provider=provider,
        code_intel_ctx=code_intel_ctx,
        map_text=map_text,
    )
    # THE CUTOVER: assemble the NEW in-meeting engine onto the boot path and stash it on
    # the runtime so the drain reaches it by meeting id. An assembly fault must not strand
    # the claimed meeting silently OR crash the webhook path — it degrades to an engine-less
    # runtime (notes plane only) with a CRITICAL log a human will see (§3.8 / Rule 6).
    try:
        engine, speak_pipe, sandbox = await _assemble_engine(
            resolved,
            db=db,
            bot_id=bot_id,
            provider=provider,
            transport=transport,
            speak=speak,
            disambiguate=disambiguate,
            sandbox_backend=sandbox_backend,
            model=model,
        )
    except Exception:  # noqa: BLE001 - never a raise on the webhook path; loud, not silent
        _log.critical(
            "in-meeting engine assembly failed for meeting %s — the meeting runs WITHOUT "
            "its brain (notes plane only); this needs a human",
            meeting_id,
            exc_info=True,
        )
    else:
        runtime.engine = engine
        runtime.speak_pipe = speak_pipe
        runtime.engine_sandbox = sandbox
        if sandbox is not None:
            # IMP-3 keep-warm: a meeting has no time cap, so the 1h-lifetime sandbox is
            # periodically extended while the meeting is live. Cancelled in
            # _teardown_engine before the kill; a failed beat logs, never crashes.
            runtime.sandbox_keepwarm = asyncio.ensure_future(
                _sandbox_keepwarm(sandbox, meeting_id)
            )
    return ProvisionOutcome(claimed=True, run_id=run_id, resumed=resumed)


def _assemble_runtime(
    payload: dict[str, Any],
    resolved: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    handle: OperationHandle,
    provider: Any = None,
    code_intel_ctx: Any = None,
    map_text: str | None = None,
) -> Any:
    """Instantiate the runtime SHELL in ONE scope + subscribe the carrier once.

    Builds the frozen §3.2 meeting header from the same webhook envelope, opens the ONE
    ``SignalCarrier``, and hands both to the registry's ``start_meeting`` — which wires
    the Scribe consumer + STT refresh on that carrier (subscribe-once at join). Then binds
    the claimed row's fencing handle onto the runtime and wires the meeting-end listener
    ONCE — the second carrier subscription, also at join, never per event.

    THE CUTOVER: the OLD live brain is DELETED — the brain seat is the NEW in-meeting
    engine, assembled by :func:`_assemble_engine` on the async chokepoint (this function
    is sync and cannot await the map load / sandbox provision). The meeting-end listener
    is the only carrier-side spine wired here: the explicit ``MeetingEnd`` signal trips
    ``run_until_meeting_end`` (zero wake turns ride the carrier). ``provider`` is accepted
    for signature compatibility with existing callers; the engine's provider is threaded
    through :func:`_assemble_engine`, not here.
    """
    _ = provider  # engine seams ride _assemble_engine; kept for caller compatibility
    from scribe.prefix import MeetingHeader
    from transport.carrier import SignalCarrier
    from transport.events import meeting_metadata

    meeting_id = str(resolved["id"])
    metadata = meeting_metadata(payload)
    header = MeetingHeader(
        meeting_id=meeting_id,
        agenda=metadata.title,
        participants=metadata.participants,
    )
    carrier = SignalCarrier()
    # start_meeting subscribes the Scribe consumer to the carrier ONCE at join. The resolved
    # ``code_intel_ctx`` (this meeting's tenant graph.db + clone) rides onto the runtime so the
    # live wake turn builds the meeting's ``code_intel`` SDK server and grounded codebase
    # questions can be answered (§11.6). None ctx → no code_intel server (honest degradation).
    runtime = registry.start_meeting(
        header, carrier, code_intel_ctx=code_intel_ctx, map_text=map_text
    )
    # Open the consent hard-gate on the LIVE hearing path (§3.1, AC-JOIN-04, Law 3): reaching
    # this assembly means the bot won the claim on a confirmed ``in_call`` event, and a bot is
    # only ``in_call`` after ``JoinSession.join`` posted the consent notice as its FIRST
    # observable action (consent-notice-first is a hard gate, not a courtesy). Before this grant
    # the live ``HearingStage`` DROPS every record (records_before_consent_allowed=0); it never
    # defaults to always-allow. An un-granted runtime (a partial/other assembly path) stays
    # fail-closed rather than silently observing pre-consent audio (F-RECORD-BEFORE-CONSENT).
    runtime.grant_consent()
    # Bind the claimed row's fencing handle so the gated emitter reads is_owner off this
    # handle (a fenced-out harness emits nothing).
    runtime.operation_handle = handle
    # THE CUTOVER: the old brain is deleted — the NEW in-meeting engine (assembled async
    # in :func:`_assemble_engine`, stashed as ``runtime.engine``) owns the wake/speak
    # seat. Wire the meeting-end listener ONCE at join (the second, and last, carrier
    # subscription): the explicit ``MeetingEnd`` signal landing on it is what ends
    # :func:`run_meeting_until_end`.
    runtime.wire_meeting_end_listener()
    return runtime


def _engine_clone_path(tenant_id: str, repo_name: str) -> Path:
    """The TENANT-ROOTED clone work-tree for this meeting's repo (the caller's duty).

    The runtime review pinned that the CALLER of ``assemble_engine`` passes a
    tenant-rooted ``clone_path``: it is ALWAYS derived from
    ``premeeting.paths.tenant_repo_dir(tenant_id, repo_name)`` (``<root>/<tenant>/repos/
    <repo>``), so one meeting's toolbelt can never name another tenant's volume — the
    cross-tenant read is unrepresentable at the path layer (PM-ISO-01). The work-tree
    itself lives one level down at ``checkout/`` (``premeeting.cloner`` materialises
    ``<repo_dir>/checkout``; ``CodeIntelContext.for_tenant_repo`` reads the same layout),
    so that is the directory the live-search tools serve from.
    """
    from premeeting.paths import tenant_repo_dir

    return Path(tenant_repo_dir(tenant_id, repo_name)) / "checkout"


def _default_meeting_transport() -> Any:
    """The REAL RecallTransport for the engine's meeting-control toolbelt.

    The harness holds no long-lived transport instance on the boot path (the invite
    path constructs one per call) — so the engine reuses the ONE production
    construction site, ``control_plane.meetings._default_transport`` (RecallTransport bound
    to the funded ``call_external`` funnel + the env/Secret-Manager config). One
    recipe, no second construction site.
    """
    from .meetings import _default_transport

    return _default_transport()


async def _assemble_engine(
    resolved: dict[str, Any],
    *,
    db: Database,
    bot_id: str,
    provider: Any = None,
    transport: Any = None,
    speak: Any = None,
    disambiguate: Any = None,
    sandbox_backend: Any = None,
    model: str | None = None,
) -> tuple[Any, Any, Any]:
    """THE CUTOVER core: assemble the NEW in-meeting engine for one claimed meeting.

    Resolves the meeting's identity off the already-fetched ``meetings`` row
    (``tenant_id``/``repo_id``/``pinned_sha``) + its repo row (``full_name`` → repo
    name), then hands ``in_meeting.runtime.assemble_engine`` its full real access:

    * ``model`` — the ORCHESTRATOR seat from ``llm.routing`` (``PROXY_MODEL_ORCHESTRATOR``
      env-overridable), never hard-coded;
    * ``clone_path`` — the TENANT-ROOTED :func:`_engine_clone_path` derivation (isolation);
    * ``speak`` — the real Cartesia→Output-Media ``SpeakPipe`` for THIS meeting
      (``real_speak_sink(meeting_id)``) unless injected;
    * ``disambiguate`` — the real async Haiku confirm (``build_disambiguator``);
    * ``transport`` — the real RecallTransport verbs (mute/unmute/post_chat/send_dm);
    * ``sandbox`` — ONE warm E2B sandbox provisioned at join. A provision FAILURE must
      NOT kill the meeting: it degrades to ``sandbox=None`` (no sandbox tools mounted,
      the caller-guard keeps names and servers aligned) with an honest log;
    * ``drafts`` — the meeting-scoped draft-staging server (Law 3: the one
      write-to-the-world stages a durable draft behind the human accept route),
      built over the durable substrate and bound to THIS meeting; a substrate that
      cannot stage mounts nothing (honest degrade, same caller-guard).

    Returns ``(engine, speak_pipe, sandbox)`` — the caller stashes all three on the
    runtime and OWNS the sandbox/pipe lifecycle (killed/closed at meeting end).
    """
    from in_meeting import disambiguator as im_disambiguator
    from in_meeting import runtime as im_runtime
    from in_meeting import sandbox as im_sandbox
    from in_meeting import speak as im_speak
    from llm.routing import model_for
    from premeeting.paths import repo_name_from_url

    meeting_id = str(resolved["id"])
    tenant_id = str(resolved.get("tenant_id") or "")
    pinned_sha = str(resolved.get("pinned_sha") or "")

    # The repo identity (name) from the SAME repo row every join-path resolver uses.
    # No repo / unknown repo → honest degrade: no map, no clone, meeting tools only.
    repo_name = ""
    repo_id = resolved.get("repo_id")
    if repo_id is not None:
        async with db.acquire() as conn:
            repo_row = await repos.meetings.get_repo_by_id(conn, repo_id)
        if repo_row is not None and repo_row.get("full_name"):
            repo_name = repo_name_from_url(str(repo_row["full_name"]))

    seat = model if model is not None else model_for("ORCHESTRATOR")
    live_transport = transport if transport is not None else _default_meeting_transport()
    speak_pipe = speak if speak is not None else im_speak.real_speak_sink(meeting_id)
    confirm = disambiguate if disambiguate is not None else im_disambiguator.build_disambiguator()

    # Warm-at-join sandbox (the engine mounts SANDBOX_TOOLS off the live handle). The
    # provisioner OWNS its lifecycle: killed in the same meeting-end teardown that
    # completes the operation row. Provisioned with SANDBOX_TIMEOUT_S (1 hour); a
    # meeting LONGER than that stays warm via the :func:`_sandbox_keepwarm` heartbeat
    # the caller spawns on a won claim (cancelled at teardown before the kill).
    sandbox: Any = None
    try:
        sandbox = await im_sandbox.provision_sandbox(
            backend=sandbox_backend,
            metadata={"meeting_id": meeting_id},
        )
    except Exception:  # noqa: BLE001 - a provision fault degrades honestly, never kills the join
        _log.warning(
            "sandbox provision failed for meeting %s — the meeting boots WITHOUT sandbox "
            "tools (honest degrade; code+meeting access unaffected)",
            meeting_id,
            exc_info=True,
        )

    # The meeting-scoped draft-staging server (Law 3 — the ONE write-to-the-world,
    # staged behind the human accept route). Built over the durable substrate facade
    # and bound to THIS meeting at build time, so a staged draft can never land in
    # another meeting. ``build_drafts_server`` returns None when the handed substrate
    # cannot stage (a stand-in db in an offline assembly) — the same caller-guard
    # honesty as code/sandbox: no server, no advertised draft tools, and a fault
    # never kills the join (§3.8 / Rule 6).
    drafts_server: Any = None
    try:
        from in_meeting import drafts_access

        drafts_server = drafts_access.build_drafts_server(db=db, meeting_id=meeting_id)
        if drafts_server is None:
            _log.warning(
                "draft staging unavailable for meeting %s — the substrate cannot stage; "
                "Proxy cannot stage a draft this meeting (honest degrade)",
                meeting_id,
            )
    except Exception:  # noqa: BLE001 - a mount fault degrades honestly, never kills the join
        _log.warning(
            "draft-staging mount failed for meeting %s — Proxy cannot stage a draft "
            "this meeting (honest degrade; other access unaffected)",
            meeting_id,
            exc_info=True,
        )

    # ``clone_repo`` must never collapse to the shared repos/ dir: a meeting with no
    # bound repo gets a definitively-nonexistent tenant-rooted path (no code server).
    clone_repo = repo_name if repo_name else "__no-repo__"
    async with db.acquire() as conn:
        engine = await im_runtime.assemble_engine(
            model=seat,
            tenant_id=tenant_id,
            repo=repo_name,
            pinned_sha=pinned_sha,
            bot_id=bot_id,
            transport=live_transport,
            conn=conn,
            clone_path=_engine_clone_path(tenant_id, clone_repo),
            speak=speak_pipe,
            disambiguate=confirm,
            provider=provider,
            sandbox=sandbox,
            drafts=drafts_server,
        )
    return engine, speak_pipe, sandbox


async def _sandbox_keepwarm(
    sandbox: Any, meeting_id: str, *, interval_s: float | None = None
) -> None:
    """Keep the meeting's warm E2B sandbox alive past its 1h self-timeout (IMP-3).

    A meeting has no time cap, but the sandbox is provisioned with
    ``SANDBOX_TIMEOUT_S`` (1h) and would silently die under a longer meeting, losing
    code execution mid-conversation. This heartbeat extends the sandbox lifetime by
    the full ``SANDBOX_TIMEOUT_S`` on every beat (the confirmed e2b
    ``AsyncSandbox.set_timeout(seconds)`` — it RESETS the lifetime from now) every
    ``SANDBOX_KEEPWARM_INTERVAL_S`` (30min — two chances per lifetime) while the
    meeting is live. The extension rides the ONE ``call_external`` seam (§14) like
    every E2B round-trip. NEVER-THROW: a failed beat logs for a human and the loop
    keeps beating (a keep-warm fault must never crash a live meeting); cancellation
    (the teardown path — cancelled in :func:`_teardown_engine` before the kill)
    propagates. ``interval_s`` is injectable for tests; the default is resolved per
    beat off the module constant so it stays patchable.
    """
    while True:
        delay = interval_s if interval_s is not None else SANDBOX_KEEPWARM_INTERVAL_S
        await asyncio.sleep(delay)
        try:
            from in_meeting.sandbox import SANDBOX_TIMEOUT_S

            from libs.http.src.http import external as _http

            await _http.call_external(
                lambda: sandbox.set_timeout(SANDBOX_TIMEOUT_S),
                service="e2b",
                max_retries=1,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never-throw: a failed beat must not kill the meeting
            _log.warning(
                "sandbox keep-warm extension failed for meeting %s — retrying on the "
                "next beat; if the sandbox lapses the meeting continues without "
                "sandbox tools (honest degrade)",
                meeting_id,
                exc_info=True,
            )


async def _resume_session(
    db: Database, meeting_id: str, *, history_fn: Any = None
) -> bool:
    """Confirm the transcript plane is reachable for the first-wake replay (§3.5).

    The replacement instance's SDK session is empty; the durable meeting history lives in
    Doc 03's Postgres transcript plane. This does NOT itself replay — the first wake
    after the swap rebuilds from the transcript-plane ``history_fn``. Here we only
    confirm that durable history plane is reachable at re-claim time, so the re-claim can
    honestly report a resumable meeting. Returns True iff the transcript plane was read.
    """
    async def _default_history() -> Any:
        # Doc 03's transcript plane (the single durable meeting-history source, §3.5):
        # the folded ``note_deltas`` are the durable meeting history a resumed session
        # rebuilds from — there is no separate SDK-session mirror.
        async with db.acquire() as conn:
            return await repos.notes.load_deltas(conn, meeting_id)

    reader = history_fn or _default_history
    with contextlib.suppress(Exception):
        # A missing/empty transcript plane must never block the re-claim: the resume is
        # best-effort (an honest "catching up" line), the claim already succeeded.
        await reader()
        return True
    return False


async def run_meeting_until_end(
    payload: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    timeout_s: float | None = None,
    resume: bool = False,
    provider: Any = None,
    transport: Any = None,
    speak: Any = None,
    disambiguate: Any = None,
    sandbox_backend: Any = None,
    model: str | None = None,
) -> ProvisionOutcome:
    """The ``asyncio.run``-style meeting entry: claim, launch the loop, run to close.

    This is what the harness process runs per meeting. It provisions (claim + assemble —
    including the NEW in-meeting engine), then LAUNCHES the meeting-end listener as the
    end-signal spine and runs until the explicit ``MeetingEnd`` signal closes the carrier.
    A meeting has NO time cap (SPEC §9): ``timeout_s=None`` (production) resolves the
    generous env-configurable safety ceiling (:func:`_meeting_max_s`, default 12h) — a
    leak backstop for a loop whose end signal never arrives, never a meeting kill; an
    explicit ``timeout_s`` is honored for tests/shutdown paths. The engine is fed by the
    webhook dispatch (push), not by an async-iterator source, so
    ``in_meeting.runtime.run_meeting`` (the pull driver) is NOT launched here. A loss (no
    claim) returns immediately without launching. On meeting end the ENGINE lifecycle
    closes first — cancel the sandbox keep-warm, drain the in-flight turns, flush+close
    the speak pipe, kill the warm sandbox, drop the Output-Media channel — then the
    runtime is torn down and the ``operation_runs`` row completes (fencing untouched).
    The engine seam kwargs thread to :func:`provision_meeting` (``None`` = the real
    vendor edges).
    """
    outcome = await provision_meeting(
        payload,
        db=db,
        registry=registry,
        resume=resume,
        provider=provider,
        transport=transport,
        speak=speak,
        disambiguate=disambiguate,
        sandbox_backend=sandbox_backend,
        model=model,
    )
    if not outcome.claimed:
        return outcome

    bot_id = _bot_id(payload)
    async with db.acquire() as conn:
        resolved = await repos.meetings.get_by_bot_id(conn, bot_id or "")
    if resolved is None:
        # A claim won but the bot no longer resolves (raced deletion) — nothing to run.
        return outcome
    meeting_id = str(resolved["id"])
    runtime = registry.get(meeting_id)
    if runtime is None:
        return outcome

    # Launch the end-signal spine: the meeting-end listener (wired ONCE at join) consumes
    # carrier signals until the explicit MeetingEnd signal lands (§3.1). No meeting time
    # cap (SPEC §9) — the resolved bound is only the generous leak-backstop ceiling
    # (env-configurable, default 12h), resolved at launch time so a deploy can raise it
    # without a code change.
    bound_s = timeout_s if timeout_s is not None else _meeting_max_s()
    ran_to_end = False
    try:
        await asyncio.wait_for(runtime.run_until_meeting_end(), timeout=bound_s)
        ran_to_end = True
    except asyncio.TimeoutError:
        ran_to_end = False
    finally:
        # meeting_end (or timeout) → close the ENGINE lifecycle first (drain turns →
        # flush+close the speak pipe → kill the sandbox → drop the Output-Media channel),
        # then run the ordered close + tear the runtime down, then complete the operation
        # row. end_meeting drains the Scribe consumer first.
        await _teardown_engine(runtime, meeting_id)
        await registry.end_meeting(meeting_id)
        await _complete_run(db, outcome.run_id)

    outcome.ran_to_end = ran_to_end
    return outcome


async def _teardown_engine(
    runtime: Any, meeting_id: str, *, timeout_s: float | None = None
) -> None:
    """Meeting-end lifecycle for the NEW engine — ordered, bounded, never-deadlock.

    Order: cancel the sandbox keep-warm heartbeat (so no beat re-extends a sandbox
    mid-destruction) → ``engine.drain()`` (every in-flight wake turn finishes or the bound trips) →
    ``speak_pipe.aclose()`` (the trailing partial is flushed into the room, then quiet) →
    ``sandbox.kill()`` (the provisioner owns the warm handle's lifetime; the kill rides
    the ONE ``call_external`` seam like every E2B round-trip) → drop the meeting's
    Output-Media channel. Every step is best-effort + WALL-CLOCK bounded (§3.8): each
    await — the kill included — rides ``asyncio.wait_for`` on the same teardown bound
    (``call_external`` retries raised transport errors but has no clock of its own, so
    a HANGING vendor edge is abandoned by the bound, not waited on). A hung turn, a
    stuck synth, or a wedged E2B kill must never block the operation-row completion
    behind it. ``timeout_s`` overrides the module bound (``ENGINE_TEARDOWN_TIMEOUT_S``)
    for callers/tests that need a tighter clock; resolved at call time.
    """
    bound = timeout_s if timeout_s is not None else ENGINE_TEARDOWN_TIMEOUT_S
    # The sandbox keep-warm heartbeat stops FIRST (before the kill): a beat racing the
    # kill could re-extend a sandbox the teardown is destroying. Cancellation is awaited
    # (suppressed) so no orphan task outlives the meeting it served.
    keepwarm = getattr(runtime, "sandbox_keepwarm", None)
    if keepwarm is not None:
        keepwarm.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await keepwarm
    engine = getattr(runtime, "engine", None)
    if engine is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(engine.drain(), timeout=bound)
    pipe = getattr(runtime, "speak_pipe", None)
    aclose = getattr(pipe, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(aclose(), timeout=bound)
    sandbox = getattr(runtime, "engine_sandbox", None)
    if sandbox is not None:
        with contextlib.suppress(Exception):
            from libs.http.src.http import external as _http

            await asyncio.wait_for(
                _http.call_external(lambda: sandbox.kill(), service="e2b", max_retries=1),
                timeout=bound,
            )
    if engine is not None or pipe is not None:
        with contextlib.suppress(Exception):
            from in_meeting import output_media

            output_media.close_channel(meeting_id)


async def _complete_run(db: Database, run_id: Any) -> None:
    """Flip the meeting's ``operation_runs`` row to completed (only if still owned)."""
    if run_id is None:
        return
    with contextlib.suppress(Exception):
        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE operation_runs "
                "SET status = 'completed', completed_at = now() "
                "WHERE id = $1 AND status = 'running'",
                run_id,
            )


def make_provision_launcher(
    db: Database,
    registry: Any,
    *,
    timeout_s: float | None = None,
    tasks: set[asyncio.Task[Any]] | None = None,
) -> Any:
    """Build the ``launch`` callback the ``meeting_runtime`` webhook drain wires in.

    Returns an async callable ``launch(payload)`` that spawns :func:`run_meeting_until_end`
    as a BACKGROUND task — the webhook drain returns 200 immediately while the meeting runs
    for hours on its own task (the RUN block survives instance recycle, §3.2). The task set
    holds a strong reference so the meeting task is never GC'd mid-flight; the done-callback
    discards it on completion. This is the ONE production caller that turns an ``in_call``
    webhook into a running, atomically-claimed meeting.
    """
    live: set[asyncio.Task[Any]] = tasks if tasks is not None else set()

    async def _launch(payload: dict[str, Any]) -> None:
        task = asyncio.ensure_future(
            run_meeting_until_end(
                payload, db=db, registry=registry, timeout_s=timeout_s
            )
        )
        live.add(task)
        task.add_done_callback(live.discard)

    return _launch


__all__ = [
    "DEFAULT_MEETING_MAX_HOURS",
    "MEETING_MAX_HOURS_ENV",
    "SANDBOX_KEEPWARM_INTERVAL_S",
    "ProvisionOutcome",
    "make_provision_launcher",
    "provision_meeting",
    "run_meeting_until_end",
]
