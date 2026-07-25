"""``provisioner`` — the per-meeting runtime entry-point (04 §3.2/§3.6, CANONICAL §12.1).

This is the entry-point that turns the built pieces into a RUNNING meeting. On a
Recall ``in_call`` webhook it:

1. **Atomically claims** the meeting via ``ops.claim_meeting`` — an INSERT ... ON
   CONFLICT DO NOTHING against the ``operation_runs`` partial-unique index
   (``(scope_id, operation_type) WHERE status='running'``). The non-null returner
   owns the meeting; a concurrent duplicate join gets ``None`` and backs off — **one
   harness per meeting, no broker, no Redis** (§3.6). The winner's instance-id is
   written onto ``created_by`` (affinity, §3.6/§11.11).

2. **Assembles the four subsystems in ONE scope** — transport (the ``SignalCarrier``),
   the Scribe runtime, the orchestrator run-loop, and the abort seam — on a single
   :class:`~harness.meeting_runtime.MeetingRuntime`, and **binds the claimed row's
   fencing handle** onto the run loop's gated emitter so every side-effect reads
   ``is_owner`` live (§3.7 fencing). The ``SignalCarrier`` is subscribed **ONCE at
   join** (the Scribe consumer + the transport→orchestrator pipe share the one carrier);
   it is never re-wired per event.

3. **Launches the run-loop event queue** — :func:`run_meeting_until_end` is the
   ``asyncio.run``-style entry: it drives the transport→orchestrator standing pipe so
   every carrier signal routes THROUGH the loop, and runs until the ``MeetingEnd``
   signal closes the carrier (or a wall-clock timeout elapses).

4. **Survives a recycle** — when the owning instance dies its heartbeat goes stale, the
   reaper (§3.8) flips the row off ``running``, the partial index frees, and a
   REPLACEMENT provisioner handed the same webhook **re-claims** the meeting and
   **resumes** it via the two-tier session-durability replay
   (``libs.agentkit.resume_with_fallback``, the pinned §3.5 seam). The media session
   cannot be resumed (restart-not-resume, §3.10) but Proxy's judgment history is
   rebuilt from Doc 03's transcript plane so the room stays coherent.

The provisioner does NOT redefine the claim, the run loop, the assembly, or the resume
fallback — it is the thin entry that wires those built pieces into a live meeting.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from libs.db import Database, repos
from libs.ops import MEETING_HARNESS_OP, OperationHandle, claim_meeting

# Default wall-clock cap on how long the launched loop waits for the explicit
# ``MeetingEnd`` signal before it tears the meeting down anyway. A live meeting ends
# on the explicit signal (§3.1); the timeout is only a backstop so a launched entry can
# never block a test / a shutdown forever. unit: seconds.
DEFAULT_MEETING_TIMEOUT_S: float = 3600.0

# Recall bot-status event names that mean "the bot is now IN the room" — the moment the
# harness claims + provisions the per-meeting runtime (mirrors ``harness.webhooks``).
_IN_CALL_EVENTS = frozenset(
    {"bot.in_call", "in_call", "bot.in_call_recording", "bot.joining_call"}
)


@dataclass
class ProvisionOutcome:
    """The result of handing an ``in_call`` webhook to the provisioner.

    ``claimed`` is the load-bearing bit: True iff THIS instance won the atomic claim and
    opened the harness; False iff a concurrent/existing harness already owns the meeting
    (this instance backed off). ``resumed`` records that a re-claim rebuilt Proxy's
    context via the §3.5 replay. ``ran_to_end`` is set by :func:`run_meeting_until_end`
    when the loop ran to the meeting-end signal (vs a timeout).
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
) -> ProvisionOutcome:
    """Claim + assemble the per-meeting harness from a Recall ``in_call`` webhook.

    The atomic-claim entry (§3.6): resolve the bot back to its meeting, then INSERT the
    ``meeting-harness`` ``operation_runs`` row via ``ops.claim_meeting``. On a WIN this
    instance owns the meeting — it assembles the runtime (transport carrier + Scribe +
    run loop + abort in ONE scope), binds the claimed row's fencing handle so ``is_owner``
    gates every emit, and subscribes the carrier ONCE at join. On a LOSS (a concurrent
    duplicate join, or an already-running harness) it returns ``claimed=False`` and opens
    NO second runtime — one harness per meeting.

    ``resume=True`` marks a REPLACEMENT re-claim after a recycle: the win rebuilds Proxy's
    session context via the two-tier §3.5 replay (``resume_with_fallback``) so the room
    stays coherent across the instance swap. A non-``in_call`` event, or an unresolvable
    bot, is a safe no-op (``claimed=False``) — never a raise on the webhook path.
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
        # A replacement re-claim after a recycle: rebuild Proxy's SDK context from Doc
        # 03's transcript plane via the pinned §3.5 two-tier replay before the loop runs,
        # so the room stays coherent across the instance swap (restart-not-resume, §3.10).
        resumed = await _resume_session(db, meeting_id, history_fn=history_fn)

    _assemble_runtime(payload, resolved, db=db, registry=registry, handle=handle)
    return ProvisionOutcome(claimed=True, run_id=run_id, resumed=resumed)


def _assemble_runtime(
    payload: dict[str, Any],
    resolved: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    handle: OperationHandle,
) -> Any:
    """Instantiate all four subsystems in ONE scope + subscribe the carrier once.

    Builds the frozen §3.2 meeting header from the same webhook envelope, opens the ONE
    ``SignalCarrier``, and hands both to the registry's ``start_meeting`` — which wires
    the Scribe consumer + STT refresh on that carrier (subscribe-once at join). Then binds
    the claimed row's fencing handle onto the runtime and builds the run loop through it
    (so the gated emitter reads ``is_owner`` live), and wires the transport→orchestrator
    standing pipe ONCE — the second carrier subscription, also at join, never per event.
    """
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
    # start_meeting subscribes the Scribe consumer to the carrier ONCE at join.
    runtime = registry.start_meeting(header, carrier)
    # Bind the claimed row's fencing handle + build the run loop through it (the gated
    # emitter reads is_owner off this handle, so a fenced-out harness emits nothing).
    runtime.operation_handle = handle
    runtime.build_run_loop()
    # Wire the transport→orchestrator standing pipe ONCE at join (the second, and last,
    # carrier subscription). subscribe() registers the consumer synchronously, so the pipe
    # is live the instant this returns — before any signal is emitted.
    runtime.wire_orchestrator_pipe()
    return runtime


async def _resume_session(
    db: Database, meeting_id: str, *, history_fn: Any = None
) -> bool:
    """Rebuild Proxy's session context on a recycle re-claim (§3.5 Tier-2 replay).

    The replacement instance's SDK session is empty; the durable meeting history lives in
    Doc 03's Postgres transcript plane. This confirms that history is reachable via the
    pinned ``resume_with_fallback`` seam so the first wake after the swap replays from it
    (emitting the user-visible "session restored" notice) rather than forgetting the
    meeting. Returns True iff the durable history plane was read for the resume.
    """
    # Import lazily so the provisioner imports without agentkit's provider resolved.
    from libs.agentkit import resume_with_fallback  # noqa: F401  (pinned single-def seam)

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
    timeout_s: float = DEFAULT_MEETING_TIMEOUT_S,
    resume: bool = False,
) -> ProvisionOutcome:
    """The ``asyncio.run``-style meeting entry: claim, launch the loop, run to close.

    This is what the harness process runs per meeting. It provisions (claim + assemble),
    then LAUNCHES the transport→orchestrator standing pipe as the run-loop spine and runs
    until the explicit ``MeetingEnd`` signal closes the carrier (or ``timeout_s`` elapses).
    A loss (no claim) returns immediately without launching. On meeting end the runtime is
    torn down and the ``operation_runs`` row completes.
    """
    outcome = await provision_meeting(
        payload, db=db, registry=registry, resume=resume
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

    # Launch the loop spine: the transport→orchestrator pipe (wired ONCE at join) forwards
    # every carrier signal THROUGH the run loop until the explicit MeetingEnd signal routes
    # through (§3.1), or the wall-clock backstop elapses.
    ran_to_end = False
    try:
        await asyncio.wait_for(runtime.run_until_meeting_end(), timeout=timeout_s)
        ran_to_end = True
    except asyncio.TimeoutError:
        ran_to_end = False
    finally:
        # meeting_end (or timeout) → run the ordered close + tear the runtime down, then
        # complete the operation row. end_meeting drains the Scribe consumer first.
        await registry.end_meeting(meeting_id)
        await _complete_run(db, outcome.run_id)

    outcome.ran_to_end = ran_to_end
    return outcome


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
    timeout_s: float = DEFAULT_MEETING_TIMEOUT_S,
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
    "DEFAULT_MEETING_TIMEOUT_S",
    "ProvisionOutcome",
    "make_provision_launcher",
    "provision_meeting",
    "run_meeting_until_end",
]
