"""DEAD END — the live in-meeting dispatch path, preserved but NOT wired.

**Nothing imports this module, and nothing should until a founder decides it should.**
It is kept as a record of working code whose substrate was deleted, not as a component.

WHAT HAPPENED. This branch built Doc 04 §112's live dispatch chain: the wake turn calls
``dispatch_workroom``, the run claims an ``operation_runs`` row, Doc 05's ``SessionDriver``
executes it, and the terminal ``Envelope`` comes back as a ``MeetingEvent`` on the meeting's
run-loop queue so Proxy re-wakes and delivers it (§3.2: *"the runtime delivers the
done-moment; nothing polls"*). It worked, and a real ordering bug in it was found and fixed
(``assemble_live_brain`` mounted the servers before the run loop existed, so an eager
``runtime.run_loop`` read meant the tool silently never mounted).

Then ``origin/main`` deleted both endpoints. ``d00c158 delete-wave 1/6: retire the silent
RunLoop spine + the old live brain`` removed ``run_loop.py`` and ``live_brain.py``, and the
new ``services/in-meeting`` engine took over the production boot path (``6a6f4a8 CUTOVER``).
There is no ``run_loop.queue`` to put a ``MeetingEvent`` on and no ``assemble_live_brain`` to
mount a tool into. This code cannot run. Making it run would mean resurrecting a spine that
was retired deliberately.

**THE STRUCTURAL REPLACEMENT IS ``in_meeting.trigger.EngagementTrigger.on_worker_done``.**
That is the same seam by a different name: *"a pure tap: a finished background worker wakes
the loop to deliver its result. No scan; an alarm clock."* It yields
``Engagement(source="worker", worker_id=..., result=...)``, which the engine turns into a
turn. So the re-wake concept survived the rewrite intact — what changed is which loop is
woken and what carries the payload (an ``Engagement``, not a ``MeetingEvent``).

**THE OPEN DECISION, which is a founder call and has not been made.** Two architectures now
exist for "Proxy does real work in a meeting":

  ours (here)     tool call -> operation_runs claim -> SessionDriver -> Envelope -> re-wake.
                  Durable: the claim is a row, so a crashed worker is recoverable and a
                  second dispatch of the same task is excluded by the partial unique index.
  his (shipped)   a warm per-meeting E2B sandbox mounted straight onto the engine turn
                  (``dcb07d3``, ``09b18a0``) plus ``mcp__drafts__propose_change``
                  (``9621e65``). Faster and simpler; no operation_runs row, so no
                  cross-process claim and no reaper story for in-meeting work.

Note that ``SessionDriver`` still has **no production caller** on either side — Daksh did not
wire it either. Whichever architecture wins, that is a live question, not a settled one.

**WHAT IS STILL WIRED AND MUST NOT BE CONFUSED WITH THIS.** The POST-MEETING dispatch path
is unaffected and remains in production: ``post_meeting/wire.py`` claims the row and runs the
task, and ``dispatch_sinks.post_meeting_sink`` records the outcome through B8's final gate.
It never touched the run loop — by construction, because after a meeting there is no room to
deliver into. Only the LIVE half died with the spine.

Restoring this would mean, at minimum: pick the architecture, port ``live_sink`` to hand its
envelope to ``on_worker_done`` instead of a queue, and find the new mount point for the tool
(the in-meeting engine assembles its own toolbelt; there is no ``_build_servers``).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable
from uuid import UUID

log = logging.getLogger(__name__)


class RunLoopUnavailable(RuntimeError):
    """A completion fired but there is no run loop to notify. Loud, never silent."""


def live_sink(
    resolve_run_loop: Callable[[], Any], *, task_id: UUID
) -> Callable[[Any], None]:
    """DEAD: the in-meeting sink — enqueue the envelope so Proxy re-wakes and delivers.

    ``harness.run_loop.MeetingEvent`` no longer exists, so the import inside ``_sink`` fails
    and every call lands in the ``BaseException`` handler. Preserved verbatim rather than
    adapted: the shape is the record of what the live path did, and guessing at an
    ``on_worker_done`` port before the architecture is chosen would be inventing a decision.

    ``resolve_run_loop`` is a callable resolved at COMPLETION time, not at mount time. That
    was a correctness requirement and is the bug fix worth carrying forward to whatever
    replaces this: ``assemble_live_brain`` built the wake turn (and therefore mounted this
    tool) BEFORE it built the run loop — it had to, because ``build_run_loop`` needed the
    wake adapter the wake turn produced. So at mount time ``runtime.run_loop`` was always
    ``None``, and an eager read made the tool silently fail to mount on every live meeting.
    Any future re-wake seam has the same hazard if it resolves its destination too early.
    """

    def _sink(envelope: Any) -> None:
        try:
            from harness.run_loop import MeetingEvent  # DEAD: module deleted on origin/main

            run_loop = resolve_run_loop()
            if run_loop is None or getattr(run_loop, "queue", None) is None:
                raise RunLoopUnavailable(
                    f"no run loop when task {task_id} completed; the room will not hear "
                    "this result. The envelope is still durable on the operation_runs row."
                )
            run_loop.queue.put_nowait(
                MeetingEvent(payload=envelope, ask_id=str(task_id))
            )
        except RunLoopUnavailable as exc:
            log.error("%s", exc)
        except asyncio.QueueFull:
            log.error(
                "meeting queue full; the completion for task %s was not enqueued. "
                "The result is still durable on the operation_runs row.",
                task_id,
            )
        except BaseException:  # noqa: BLE001 - a failed notify must not lose the run
            log.exception("could not enqueue the completion for task %s", task_id)

    return _sink


def build_dispatch_server(runtime: Any) -> dict[str, Any] | None:
    """DEAD: mount §112's ``dispatch_workroom`` tool for one meeting's wake turn.

    Was ``live_brain._build_dispatch_server``. ``live_brain.py`` is deleted, and the
    in-meeting engine assembles its own toolbelt rather than merging a dict of SDK MCP
    servers, so there is no caller and no equivalent mount point to move this to.

    The NOTE below is the fix for the silent-no-mount bug and is the part worth reading if
    this is ever revived.
    """
    db = getattr(runtime, "db", None)
    if db is None:
        log.error(
            "dispatch_workroom NOT mounted: the runtime has no db handle, so no "
            "operation_runs row can be claimed. Proxy cannot dispatch work this meeting."
        )
        return None
    # NOTE: runtime.run_loop is deliberately NOT read here — see live_sink's docstring.
    try:
        from datetime import datetime, timezone

        from harness.dispatch import make_dispatch_workroom_server  # DEAD: member dissolved

        meeting_id = runtime.header.meeting_id

        def _run_task(bundle: Any, *, run_id: Any) -> Any:
            from workroom.session import SessionDriver

            return SessionDriver(db=db, disposition="worker").run_task(
                bundle, run_id=run_id
            )

        def _on_complete(envelope: Any) -> None:
            live_sink(
                lambda: getattr(runtime, "run_loop", None), task_id=envelope.task_id
            )(envelope)

        server = make_dispatch_workroom_server(
            db=db,
            meeting_id=meeting_id,
            now=lambda: datetime.now(timezone.utc),
            run_task=_run_task,
            on_complete=_on_complete,
        )
    except Exception:  # noqa: BLE001 - never crash the meeting over a mount
        log.exception("dispatch_workroom NOT mounted: the server could not be built")
        return None
    return {"dispatch_workroom": server}
