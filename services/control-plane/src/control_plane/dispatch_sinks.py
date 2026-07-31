"""The post-meeting completion sink (§112).

``dispatch.run_and_notify`` fires one synchronous callback with the terminal
:class:`~contracts.Envelope`. After a meeting there is no room to deliver it into — no
``Emitter``, no wake turn — so the envelope goes to Doc 07's task record via
``run_final_gate``, and the draft card is what surfaces it.

**There used to be a second sink here.** ``live_sink`` put the envelope on the meeting run
loop's queue so Proxy re-woke and delivered it live. ``origin/main`` deleted the run loop and
the live brain (``d00c158``), so that sink has no destination and has moved, unwired, to
``live_dispatch_deadend`` along with the tool mount. Which architecture replaces it is an
open founder decision — see that module. Nothing in the post-meeting path depended on it.

This sink is **synchronous**: it is called from inside an ``asyncio`` done-callback on the
event loop, so it may only hand off, never await. It schedules its write and returns.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable
from uuid import UUID

log = logging.getLogger(__name__)


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
