"""The TWO completion sinks a dispatched Workroom task can land in (§112).

``dispatch.run_and_notify`` fires one synchronous callback with the terminal
:class:`~contracts.Envelope`. Where that envelope goes differs between the two callers,
and the difference is not cosmetic:

* **Live, in-meeting** — the room is still there. The envelope becomes a
  :class:`~harness.run_loop.MeetingEvent` on the meeting's queue, Proxy re-wakes, and the
  wake turn delivers through the ``Emitter`` (§3.2: *"the runtime delivers the done-moment;
  nothing polls"*).
* **Post-meeting** — the bot has left. There is no room, no ``Emitter`` and no wake turn,
  so the envelope goes to Doc 07's task record via ``run_final_gate``, and the draft card
  is what surfaces it.

Both sinks are **synchronous**. They are called from inside an ``asyncio`` done-callback on
the event loop, so they may only hand off — never await. The live sink uses
``put_nowait``; the post-meeting sink schedules its write and returns.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable
from uuid import UUID

log = logging.getLogger(__name__)


class RunLoopUnavailable(RuntimeError):
    """A completion fired but there is no run loop to notify. Loud, never silent."""


def live_sink(
    resolve_run_loop: Callable[[], Any], *, task_id: UUID
) -> Callable[[Any], None]:
    """The in-meeting sink: enqueue the envelope so Proxy re-wakes and delivers.

    **``resolve_run_loop`` is a callable, resolved at COMPLETION time, not at mount time.**
    That is a correctness requirement, not a style choice. ``assemble_live_brain`` builds
    the wake turn (and therefore mounts this tool) at line ~501 and only builds the run loop
    at ~515 — it cannot do otherwise, because ``build_run_loop`` needs the wake adapter the
    wake turn produces. So at mount time ``runtime.run_loop`` is always ``None``. An eager
    read here made the tool silently fail to mount on every live meeting. A task completion
    necessarily happens long after assembly, so resolving late always sees the built loop.

    ``put_nowait`` and nothing else. An ``await`` here would block the event loop inside a
    done-callback, and an unbounded queue means there is nothing to wait for anyway.

    A missing run loop at completion time raises :class:`RunLoopUnavailable` into the log at
    ERROR rather than passing quietly — a dropped completion means the room never hears the
    result, which is exactly the class of silence this whole seam exists to remove. A full
    queue is also logged: the run itself completed and its envelope is durable on the
    ``operation_runs`` row, so losing the *notification* must not look like losing the *work*.
    """

    def _sink(envelope: Any) -> None:
        try:
            from .run_loop import MeetingEvent

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


def post_meeting_sink(
    *,
    task_id: UUID,
    store: Any,
    draft_row_for: Callable[[Any], Any] | None = None,
    bundle_exists: Callable[[Any], bool] | None = None,
) -> Callable[[Any], None]:
    """The post-meeting sink: record the outcome on Doc 07's task record.

    Delegates to ``post_meeting.final_gate.run_final_gate`` — B8 already owns the rules for
    what may be recorded (a ``staged_drafts`` row at ``'proposed'`` with a retrievable
    bundle, and never a push). This sink does not re-decide any of that; it only gets the
    envelope to the block that does.

    ``run_final_gate`` is async and this sink is sync, so the write is SCHEDULED with
    ``ensure_future`` and the sink returns immediately. The task handle is held in the
    module set for the same reason ``run_and_notify`` holds its own: an unheld task can be
    garbage-collected mid-write.

    ``draft_row_for`` and ``bundle_exists`` MAY return awaitables, and they are resolved
    inside the scheduled coroutine rather than here. That is not a convenience: the draft row
    is a ``staged_drafts`` read against Postgres, and a synchronous callback on the event
    loop cannot do one. Resolving them eagerly in this sink would force the caller to have
    the row in hand before the run even finished — which it cannot, because the ``draft_id``
    only exists once the task has proposed it.
    """
    _pending: set[Any] = _POST_MEETING_WRITES

    async def _record(envelope: Any) -> Any:
        from .post_meeting.final_gate import run_final_gate

        draft_row = await _resolve(draft_row_for, envelope) if draft_row_for else None
        exists = (
            await _resolve(bundle_exists, envelope)
            if bundle_exists
            else draft_row is not None
        )
        return await run_final_gate(
            task_id=task_id,
            envelope=envelope,
            draft_row=draft_row,
            bundle_exists=bool(exists),
            store=store,
        )

    def _sink(envelope: Any) -> None:
        try:
            fut = asyncio.ensure_future(_record(envelope))
            _pending.add(fut)
            fut.add_done_callback(_on_recorded)
        except BaseException:  # noqa: BLE001 - the run completed; bookkeeping must not fail it
            log.exception("could not record the post-meeting outcome for task %s", task_id)

    def _on_recorded(fut: Any) -> None:
        """Discard the handle AND surface a failure — a swallowed write is the old bug."""
        _pending.discard(fut)
        if not fut.cancelled() and fut.exception() is not None:
            log.error(
                "the post-meeting outcome for task %s was not recorded: %r",
                task_id, fut.exception(),
            )

    return _sink


async def _resolve(fn: Callable[[Any], Any], envelope: Any) -> Any:
    """Call ``fn(envelope)`` and await the result if it is awaitable."""
    result = fn(envelope)
    if inspect.isawaitable(result):
        return await result
    return result


#: Strong refs to in-flight post-meeting outcome writes (see ``dispatch._INFLIGHT``).
_POST_MEETING_WRITES: set[Any] = set()
