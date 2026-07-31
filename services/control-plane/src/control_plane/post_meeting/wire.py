"""The real ``dispatch=`` for SEAM 2 — what the approve route was missing.

``control_plane.plan_approval_route`` has always taken a ``dispatch`` callable. Until now
nothing could be passed, so ``dispatch=None`` raised ``WorkroomDispatchUnavailable`` and the
route answered 202 ``dispatch_blocked``. Doc 04 §112's wrapper now exists, so this module is
the thing that goes in that parameter and the raise path is gone.

**What it composes, and nothing more.** Every piece already existed and had been verified in
isolation; the defect was that no line of production code joined them:

  ``run_dispatch``      B6's decision — approval, then caps, then cost. Injected with the
                        real ``assemble_bundle`` and a real workroom dispatcher, which is
                        what B6's docstring always said it needed and never got.
  ``dispatch_workroom`` Doc 04's ``operation_runs`` claim (P10: ``scope_id`` = meeting id,
                        ``operation_type`` = ``workroom:{task_id}``).
  ``SessionDriver``     Doc 05's driver that actually runs the task. It was complete and
                        constructed only in ``tests/doc05/*``. This is its first caller.
  ``run_and_notify``    §112's ``create_task`` + done-callback, with the strong reference
                        that keeps a dispatched run from being garbage-collected mid-flight.
  ``post_meeting_sink`` where the terminal envelope lands when there is no meeting left to
                        deliver it into: Doc 07's task record, via B8's final gate.

**The route's connection is deliberately ignored.** ``handle_approve_plan`` runs inside the
route's ``async with db.acquire()``, and that connection is released the moment the route
returns. The dispatched work outlives the request, so it must own its own connections — it
takes the ``db`` handle bound here at install time instead. Using the route's connection
would give a use-after-release that only shows up under load.

**Approval is synchronous; execution is not.** The route returns as soon as the human's
decision is durable. It does not wait for the sandbox — waiting would hold a request open
for the length of a code change, and §3.4's promise is that the *decision* is recorded, not
that the work is finished. The draft card is what surfaces the outcome (§3.8).

**There is still no poller, scheduler or queue.** The human's click is the only trigger; this
module is called from the click's handler and from nowhere else.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

#: Strong refs to scheduled dispatch chains. An unheld task can be collected mid-run —
#: the same weak-reference trap ``dispatch.run_and_notify`` and ``provisioner.py:367`` guard.
_SCHEDULED: set[Any] = set()


def make_intake_hook(
    db: Any,
    *,
    caller: Any = None,
    call_external: Any = None,
    channels: Any = (),
    config: Any = None,
) -> Callable[..., Any]:
    """Build SEAM 1's supplier — the callable ``CloseConfig.post_meeting_intake`` wants.

    The seam site has existed since B1 landed: ``scribe_runtime`` calls
    ``_run_post_meeting_intake`` immediately after ``run_close_pass`` returns. What never
    existed was anything to put in the field, so the hook was ``None`` on every production
    close, the seam returned early, and ``run_extract`` / ``run_triage`` had **no production
    caller at all**. The tests passed because they injected a hook. That is the same
    false-green shape as a store-backed test skipping without a DSN: a wire that looks
    connected, proves itself against its own stand-in, and is dead in production.

    Returns ``hook(final_notes, *, meeting_id)`` — the exact shape the seam invokes. The
    tenant is resolved SERVER-SIDE from the meeting row; nothing about intake trusts a
    caller-supplied tenant. ``caller``/``call_external`` default to the real Anthropic
    structured caller and the one ``libs.http`` funnel, so production needs to pass only
    ``db``; tests pass fakes.

    This function does not guard against failure and does not need to: the seam calls it
    through ``_run_post_meeting_intake``, which catches ``BaseException``, and intake itself
    runs under ``run_intake_guarded``. Doc 07 §2's promise — a total intake failure leaves
    the close and the meeting record identical — is held in those two places, not here.
    """

    async def _hook(final_notes: Any, *, meeting_id: Any) -> Any:
        from libs.llm.src.llm.structured import anthropic_structured_caller

        from .intake import run_intake_guarded
        from .store import ClarifyItemStore, PostMeetingTaskStore

        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tenant_id FROM meetings WHERE id = $1", meeting_id
            )
        if row is None:
            # A close ran for a meeting that is not in the table. Loud: every task intake
            # would write is tenant-scoped, and there is no safe default tenant to invent.
            log.error(
                "post-meeting intake skipped: meeting %s has no row, so its tenant cannot "
                "be resolved and no task may be written",
                meeting_id,
            )
            return None

        resolved_caller = caller
        if resolved_caller is None:
            resolved_caller = anthropic_structured_caller()
        resolved_call_external = call_external
        if resolved_call_external is None:
            from libs.http.src.http.external import call_external as real_call_external

            resolved_call_external = real_call_external

        return await run_intake_guarded(
            final_notes,
            meeting_id=meeting_id,
            tenant_id=row["tenant_id"],
            task_store=PostMeetingTaskStore(db),
            clarify_store=ClarifyItemStore(db),
            caller=resolved_caller,
            call_external=resolved_call_external,
            channels=channels,
            config=config,
        )

    return _hook


def make_plan_dispatcher(
    *,
    db: Any,
    store: Any = None,
    now: Optional[Callable[[], Any]] = None,
    config: Any = None,
    estimate_cost: Any = None,
) -> Callable[..., None]:
    """Build the callable ``install_approve_route(dispatch=...)`` wants.

    The returned function has the route's shape — ``dispatch(conn, *, task_id, meeting_id)``
    — and returns ``None`` immediately after scheduling. It never raises: a dispatch that
    cannot start must not unwind an approval that already landed, so every failure is logged
    and recorded on the task record rather than thrown back at the route.
    """
    from .store import PostMeetingTaskStore

    task_store = store if store is not None else PostMeetingTaskStore(db)
    clock = now if now is not None else (lambda: datetime.now(timezone.utc))

    def _dispatch(conn: Any = None, *, task_id: Any, meeting_id: Any) -> None:
        # ``conn`` is accepted for the route's signature and NOT used — see the module
        # docstring. The scheduled chain acquires its own connections from ``db``.
        del conn
        try:
            fut = asyncio.ensure_future(
                _dispatch_now(
                    db=db, store=task_store, clock=clock, config=config,
                    estimate_cost=estimate_cost, task_id=task_id, meeting_id=meeting_id,
                )
            )
            _SCHEDULED.add(fut)
            fut.add_done_callback(_on_scheduled_done)
        except BaseException:  # noqa: BLE001 - the approval stands regardless
            log.exception(
                "could not schedule dispatch for task %s; the approval is durable and the "
                "task is still APPROVED, so it can be re-dispatched by hand",
                task_id,
            )

    return _dispatch


def _on_scheduled_done(fut: Any) -> None:
    """Release the handle and make a failed chain visible. Silence here was the old bug."""
    _SCHEDULED.discard(fut)
    if fut.cancelled():
        log.error("a dispatch chain was cancelled before it finished")
        return
    exc = fut.exception()
    if exc is not None:
        log.error("a dispatch chain raised: %r", exc)


async def _dispatch_now(
    *,
    db: Any,
    store: Any,
    clock: Callable[[], Any],
    config: Any,
    estimate_cost: Any,
    task_id: Any,
    meeting_id: Any,
) -> Any:
    """Read the approved task, then run B6's decision with the real injections."""
    from .dispatch import run_dispatch

    row = await store.get(task_id)
    if row is None:
        log.error("dispatch: task %s vanished between approval and dispatch", task_id)
        return None

    # The ASK is the approved PLAN, not the raw extracted item. What the human approved is
    # what may run — dispatching the original text would execute something nobody read.
    ask = str(row.get("plan") or "").strip()
    if not ask:
        log.error(
            "dispatch: task %s is APPROVED with no plan; nothing to execute. B4 must set a "
            "plan before B5 can approve, so this is a broken task record, not a dispatch bug",
            task_id,
        )
        return None

    # The speaker of record is the approver where there is one, else the task's owner. A
    # dispatched run is attributable to a human either way (§3.4).
    speaker = str(row.get("approved_by") or row.get("owner") or "").strip()

    return await run_dispatch(
        task_id=task_id,
        tenant_id=row.get("tenant_id"),
        meeting_id=meeting_id,
        ask=ask,
        speaker=speaker,
        timestamp=clock(),
        store=store,
        workroom_dispatch=_make_workroom_dispatch(db=db, store=store, task_id=task_id),
        assemble_bundle=_assemble_bundle,
        estimate_cost=estimate_cost,
        config=config,
    )


def _assemble_bundle(**kwargs: Any) -> Any:
    """Doc 04's bundle, unchanged. Imported here so B6 keeps importing neither side."""
    from ..dispatch import assemble_bundle

    return assemble_bundle(**kwargs)


def _make_workroom_dispatch(*, db: Any, store: Any, task_id: Any) -> Callable[[Any], Any]:
    """Claim the ``operation_runs`` row, then start the run and route its completion.

    ``dispatch_workroom`` only CLAIMS; it does not execute. That split is deliberate in Doc
    04 — the claim is the durable fact, the execution is the work — but it means a caller
    that claims and stops leaves a row at ``running`` that nothing will ever finish. That is
    exactly the dead end the old ``WorkroomDispatchUnavailable`` refused to create. So the
    claim and the run are joined here, in one place, and the handle is returned to B6 so it
    can record ``operation_ref`` from the run row's real id.
    """

    async def _dispatch(bundle: Any) -> Any:
        from ..dispatch import WorkroomHandle, dispatch_workroom, run_and_notify
        from ..dispatch_sinks import post_meeting_sink

        handle = await dispatch_workroom(db, bundle)
        if not isinstance(handle, WorkroomHandle):
            # The cost gate ran and asked instead of dispatching, so no row was claimed.
            # B6 reads the missing run_id and reports it; there is nothing to run.
            return handle

        from workroom.session import SessionDriver

        run_and_notify(
            SessionDriver(db=db, disposition="worker").run_task(
                bundle, run_id=handle.run_id
            ),
            task_id=bundle.task_id,
            on_complete=post_meeting_sink(
                task_id=task_id,
                store=store,
                draft_row_for=_draft_row_reader(db),
            ),
            label="post-meeting-workroom",
        )
        return handle

    return _dispatch


def _draft_row_reader(db: Any) -> Callable[[Any], Any]:
    """Read the ``staged_drafts`` row the finished task proposed.

    B8's final gate validates a row it is HANDED; it does not go looking for one (§3.8 puts
    staging in Doc 05's hands, not Doc 07's). The envelope carries the ``draft_id``, so this
    is the join between the two — and it is an async read, which is why
    ``post_meeting_sink`` resolves it inside its scheduled coroutine.

    No draft id means the task produced no artifact. That is not an error here: the gate
    treats a missing row as "nothing to accept" and records the honest failure.
    """

    async def _read(envelope: Any) -> Optional[dict[str, Any]]:
        draft_id = getattr(envelope, "draft_id", None)
        if draft_id is None:
            return None
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT draft_id, meeting_id, kind, summary, artifact_ref, status "
                "  FROM staged_drafts WHERE draft_id = $1",
                draft_id,
            )
        return dict(row) if row is not None else None

    return _read
