"""``workroom_bridge`` — the harness→Workroom DISPATCH bridge (Doc 04 §11.6 reactive
flow + Doc 05 §3.2/§3.9).

The confirmed hole this closes: a wake-turn model calling ``dispatch_workroom`` yields a
``TOOL_USE`` chunk that the pure channel projector renders as a "working…" tile line and
``_emit_frame`` drops — so the REAL ``harness.dispatch.dispatch_workroom`` was NEVER called,
no Workroom ran, and Proxy was never re-woken with the result. NOTHING in the harness ever
constructed a :class:`~workroom.session.SessionDriver` or drove ``run_task``.

This module is the thin bridge. It redefines NONE of the primitives (dispatch, the
WorkroomHandle, the SessionDriver, the objectstore, the gated Emitter are all imported and
wired, never rebuilt). Three pieces:

1. :func:`is_dispatch_tool_use` — the mechanical predicate that recognizes a
   ``dispatch_workroom`` TOOL_USE chunk on the wake delta stream (§3.4 selection is by name,
   never a code branch on WHAT to do — this is pure recognition, Law 4).
2. :func:`build_workroom_driver` — construct the ONE :class:`SessionDriver` for this meeting,
   bound to the runtime's db + abort registry, the injected provider, and the workroom
   ``objectstore`` store. Fail-closed: it never opens a sandbox itself (``run_task`` resolves
   the warm sandbox lazily) and never raises on a fake/absent db.
3. :func:`handle_dispatch` — the reactive flow: extract the ask, assemble the Bundle, claim
   the durable ``workroom:<task_id>`` row via the REAL :func:`~harness.dispatch.dispatch_workroom`
   (ungated), ACK "on it: …" through the gated emitter immediately (§3.2), register the
   done-moment delivery callback (§3.2 push, never poll), and drive ``run_task`` in a tracked
   background task. Everything is is_owner-gated via the emitter (a fenced-out harness delivers
   nothing) and every fault becomes an honest failed-Envelope delivery — the wake loop never
   sees an unhandled exception (Rule 6).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from contracts import Bundle, Envelope

from .dispatch import WorkroomHandle, assemble_bundle, dispatch_workroom

#: The wake tool name whose TOOL_USE chunk means "dispatch this ask to the Workroom" (§11.6).
DISPATCH_TOOL_NAME = "dispatch_workroom"

#: The keys a model-authored dispatch input dict may carry the task text under, in priority
#: order — read DEFENSIVELY (the input is model-authored free-form, never a fixed schema).
_ASK_KEYS: tuple[str, ...] = ("task", "ask", "text", "description")


def is_dispatch_tool_use(chunk: Any) -> bool:
    """True iff ``chunk`` is a ``dispatch_workroom`` TOOL_USE chunk on the wake stream (§11.6).

    The wake stream carries an :class:`~contracts.AgentChunk` per delta; a tool call is
    ``chunk.type == "TOOL_USE"`` with ``chunk.metadata["name"]`` the tool name (provider.py:171).
    A pure structural read (duck-typed on ``.type`` / ``.metadata``) so a malformed chunk can
    never crash the predicate — anything that is not a well-formed dispatch TOOL_USE is False.
    """
    if getattr(chunk, "type", None) != "TOOL_USE":
        return False
    metadata = getattr(chunk, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    return metadata.get("name") == DISPATCH_TOOL_NAME


def _extract_ask(chunk: Any) -> str:
    """Read the task text off a dispatch chunk's model-authored ``input`` dict, defensively.

    ``chunk.metadata["input"]`` is a free-form dict the model authored (§3.4 / D-023 — the
    ask is the model's, the plumbing is ours, Law 4). Try the known ask keys in priority
    order; fall back to ``str(input)`` so a differently-shaped input still yields a non-empty
    ask rather than dropping the dispatch. A missing/empty input yields ``""``.
    """
    metadata = getattr(chunk, "metadata", None)
    if not isinstance(metadata, dict):
        return ""
    payload = metadata.get("input")
    if isinstance(payload, dict):
        for key in _ASK_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        # A non-empty dict with no known key: render it (never drop a real dispatch).
        return str(payload) if payload else ""
    if isinstance(payload, str):
        return payload
    return str(payload) if payload else ""


def build_workroom_driver(runtime: Any, *, provider: Any) -> Any:
    """Construct THE :class:`~workroom.session.SessionDriver` for this meeting's dispatches.

    Bound to the runtime's durable ``db`` + shared ``abort_registry`` (§11.9), the injected
    ``provider`` seam (the SAME model talker the wake turn uses), and the workroom
    ``objectstore`` as the durable draft ``store``. Runs as ``disposition="worker"`` (§3.2 /
    D-014 — the reactive dispatch is a worker task). It NEVER opens a sandbox here: the warm
    sandbox is resolved lazily inside ``run_task`` via ``sandbox_provider.provision`` (and
    ``close.py`` owns teardown), so this bridge manages no sandbox lifecycle.

    Fail-closed (Rule 6): a fake/absent ``runtime.db`` still yields a driver — ``run_task``
    never raises, so a broken db surfaces as a ``failed`` Envelope at run time, not a crash
    here. Returns the constructed driver.
    """
    from workroom import objectstore
    from workroom.session import SessionDriver

    return SessionDriver(
        provider=provider,
        store=objectstore,
        db=getattr(runtime, "db", None),
        abort_registry=getattr(runtime, "abort_registry", None),
        disposition="worker",
    )


def _deliver(env: Any, emitter: Any) -> None:
    """The done-moment delivery (§3.2): speak the terminal Envelope through the gated emitter.

    Mechanical (physics/pipes, no new model judgment, Law 4): speak the Envelope's
    ``headline``, then its body (``detail`` — the answer prose) if present and non-empty. Both
    go through the gated ``emitter.speak`` (is_owner fencing, §3.7): a fenced-out harness
    delivers nothing. Tolerant of a non-Envelope (defensive) — it speaks only what is present.
    """
    if emitter is None:
        return
    headline = getattr(env, "headline", None)
    if isinstance(headline, str) and headline:
        emitter.speak(headline)
    detail = getattr(env, "detail", None)
    if isinstance(detail, str) and detail.strip():
        emitter.speak(detail)


async def handle_dispatch(
    chunk: Any,
    *,
    runtime: Any,
    event: Any,
    driver: Any,
    emitter: Any,
    db: Any,
    tasks: set[Any],
) -> None:
    """React to a ``dispatch_workroom`` tool call: run a Workroom task, deliver the result (§11.6).

    The reactive flow, all is_owner-gated via ``emitter`` and fail-closed (Rule 6 — the wake
    loop must NEVER see an unhandled exception from here):

    1. extract the ask off the model-authored input;
    2. assemble the ``contracts.Bundle`` (notes_ref = the meeting_id UUID, §1.3) — a non-UUID
       meeting_id skips the dispatch honestly (never raises into the wake loop);
    3. claim the durable ``workroom:<task_id>`` row via the REAL ``dispatch_workroom`` (ungated
       → a bare :class:`WorkroomHandle`); a non-handle return bails gracefully;
    4. ACK immediately — speak ``"on it: <ask>"`` (the handle's partial-Envelope headline, §3.2);
    5. register the done-moment delivery callback (a push, never a poll);
    6. drive ``run_task`` in a TRACKED background task (added to ``tasks`` so it is never GC'd
       mid-flight and close can cancel it), guarded so a driver fault still delivers a
       failed-Envelope rather than an unhandled task exception.
    """
    ask = _extract_ask(chunk)

    # notes_ref IS the meeting_id UUID (§1.3). A non-UUID meeting_id (e.g. a test's "mtg-1")
    # can't key a durable row — skip the dispatch honestly; NEVER raise into the wake loop.
    raw_meeting_id = getattr(getattr(runtime, "header", None), "meeting_id", None)
    try:
        meeting_uuid = UUID(str(raw_meeting_id))
    except (ValueError, TypeError, AttributeError):
        return

    bundle: Bundle = assemble_bundle(
        ask=ask,
        speaker=str(getattr(event, "speaker", "") or ""),
        timestamp=datetime.now(tz=UTC),
        meeting_id=meeting_uuid,
        transcript_tail=str(getattr(event, "text", "") or ""),
        task_id=uuid4(),
    )

    # Claim the durable row via the REAL dispatch (ungated → a bare WorkroomHandle). A db
    # fault here is fatal to THIS dispatch only — swallow it honestly (Rule 6), never crash
    # the wake loop.
    try:
        handle = await dispatch_workroom(db, bundle)
    except Exception:  # noqa: BLE001 - a claim fault degrades to no-dispatch, never crashes the wake loop
        return
    if not isinstance(handle, WorkroomHandle):
        # The gated estimate path returns a DispatchDecision, not a handle — we called it
        # UNGATED, so a non-handle here is defensive: bail gracefully.
        return

    # ACK immediately (§3.2): the partial "on it: <ask>" headline, gated on is_owner.
    if emitter is not None:
        emitter.speak(handle.as_envelope().headline)

    # Register the done-moment delivery (§3.2 push): when the terminal Envelope lands,
    # _deliver speaks it through the gated emitter. Mechanical — no new model judgment.
    handle.on_complete(lambda env: _deliver(env, emitter))

    # Drive the Workroom in a TRACKED background task so it is never GC'd mid-flight and close
    # can cancel it. run_task never raises (it returns a failed Envelope on any fault); the
    # extra guard covers a defective driver so a fault still delivers, never an unhandled task
    # exception (Rule 6).
    async def _drive() -> None:
        try:
            env = await driver.run_task(bundle, run_id=handle.run_id)
        except Exception as exc:  # noqa: BLE001 - Rule 6: a driver fault still delivers a failed Envelope
            env = Envelope(
                headline=f"couldn't finish: {ask}" if ask else "couldn't finish the task",
                detail=str(exc),
                status="failed",
                task_id=bundle.task_id,
            )
        handle.set_result(env)

    task = asyncio.ensure_future(_drive())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


__all__ = [
    "DISPATCH_TOOL_NAME",
    "build_workroom_driver",
    "handle_dispatch",
    "is_dispatch_tool_use",
]
