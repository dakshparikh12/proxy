"""The ordered close (§3.16) — the meeting-end sequence, on ONE operation row.

On meeting-end (the in-place close — there is no separate close doc) Proxy runs
the ordered close. It is its OWN ``operation_runs`` row (``operation_type=
'meeting-close'``, completion predicate = the notes.md object exists — there is
**no ``close_jobs`` table**, CANONICAL §12.10), which makes the close crash-safe
and idempotent: re-running a completed close hits ``if_generation_match=0`` on the
already-written notes object and is a no-op (the existing URL is reused).

The order is MANDATORY and load-bearing (§3.16):

    freeze  →  close-pass  →  destroy-sandbox  →  complete-harness-row  →  teardown-pipes LAST

1. **freeze** — the serial Scribe consumer drains (the ``MeetingEnd`` signal pushed
   the sentinel), so the durable ``note_deltas`` ledger is complete and frozen
   before the close pass folds it. No delta is appended past this point.
2. **close-pass** — Doc 03's close pass (``run_meeting_close``): fold the ledger,
   reduce it through the strong-model close, write the notes markdown to GCS
   ``if_generation_match=0`` (create-only, never overwrite), and post the link in
   the meeting chat. This is the whole V0 close deliverable.
3. **destroy-sandbox** — the per-meeting sandbox is explicitly destroyed (§3.9,
   one of the three sandbox bounds), and the meeting is marked ended so the
   reconcile cron reaps any sandbox that somehow survived (defence #3).
4. **complete-harness-row** — the meeting-harness ``operation_runs`` row is flipped
   to ``completed`` (§3.7), BEFORE any pipe is torn down.
5. **teardown-pipes LAST** — only now are the standing pipes + carrier torn down
   (``MeetingRuntime.aclose``). Nothing reads a torn-down store: the notes object,
   the chat link, and the completed row are all durable before teardown, and the
   staged drafts (``staged_drafts`` + GCS-versioned content, CANONICAL §4) live in
   durable state that OUTLIVES teardown so the accept-handler still works after the
   call.
"""
from __future__ import annotations

import contextlib
from typing import Any

from libs.ops import claim_meeting, sandbox_provider

MEETING_CLOSE_OP = "meeting-close"


async def _complete_harness_row(runtime: Any) -> None:
    """Flip the meeting-harness ``operation_runs`` row to ``completed`` (§3.7).

    Runs BEFORE any pipe is torn down. Uses the bound ``operation_handle`` (the
    claimed meeting-harness row) and only completes a row this instance still owns
    (``status='running'`` guard) — a fenced-out or already-completed row is a
    no-op, so the step is idempotent and never clobbers a re-claim. Best-effort:
    a DB blip here must not block the LAST teardown of the pipes.
    """
    handle = getattr(runtime, "operation_handle", None)
    run_id = getattr(handle, "run_id", None) if handle is not None else None
    db = getattr(runtime, "db", None)
    if run_id is None or db is None:
        return
    with contextlib.suppress(Exception):
        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE operation_runs "
                "SET status = 'completed', completed_at = now() "
                "WHERE id = $1 AND status = 'running'",
                run_id,
            )


async def _destroy_sandbox(runtime: Any, sandbox: Any) -> None:
    """Destroy the meeting's sandbox (§3.9) + mark the meeting ended for the cron.

    Idempotent: ``sandbox_provider.destroy`` tolerates an already-gone sandbox
    (404 → 'gone'); ``mark_meeting_ended`` lets the TTL/orphan reconcile reap any
    sandbox that survived the explicit close (the cost backstop, not correctness).
    Best-effort — a destroy failure must not block completing the row or the LAST
    pipe teardown. When no handle is threaded through, the live sandbox for this
    meeting is resolved from the provider's live view and destroyed there.
    """
    meeting_id = getattr(getattr(runtime, "header", None), "meeting_id", None)
    with contextlib.suppress(Exception):
        target = sandbox
        if target is None and meeting_id is not None:
            target = next(
                (h for h in sandbox_provider.list_sandboxes() if h.meeting_id == str(meeting_id)),
                None,
            )
        if target is not None:
            await sandbox_provider.destroy(target)
    if meeting_id is not None:
        with contextlib.suppress(Exception):
            sandbox_provider.mark_meeting_ended(str(meeting_id))


async def run_ordered_close(
    runtime: Any,
    close_config: Any,
    *,
    sandbox: Any = None,
) -> Any:
    """Run the §3.16 ordered close over Doc 03's close pass — the wired meeting-end.

    The close pass writes the notes object + posts the chat link (steps 1-2 of the
    order); this function threads the ordered TAIL as the close pass's ``teardown``
    callback so the mandatory order holds end-to-end:

        freeze → close-pass → destroy-sandbox → complete-harness-row → teardown-pipes LAST

    The tail (destroy-sandbox → complete-harness-row → teardown-pipes) is handed to
    ``run_meeting_close`` as its ``teardown`` so it runs AFTER the chat link is
    posted but BEFORE ``run_close_pass`` returns — which is where the close-op row
    is marked completed. Pipes are the LAST thing torn down, AFTER the harness row
    is completed: nothing ever reads a torn-down store.

    An EMPTY ledger (a meeting that produced no notes) means ``run_meeting_close``
    returns ``None`` without invoking the tail — so this function runs the ordered
    tail itself, guaranteeing the sandbox is destroyed, the harness row completed,
    and the pipes torn down exactly once even on the no-notes path. Idempotent: a
    re-run over an already-completed close writes no second notes object and no
    second row transition.

    Returns the ``CloseResult`` (or ``None`` on the empty-ledger path).
    """
    ran_tail = False

    async def _ordered_tail() -> None:
        # The mandatory tail of the order — destroy sandbox → complete the harness
        # row → tear the pipes down LAST. Invoked as the close pass's teardown (so it
        # runs strictly AFTER the chat-link post), or directly on the empty-ledger
        # path below. Guarded so it runs exactly once.
        nonlocal ran_tail
        if ran_tail:
            return
        ran_tail = True
        await _destroy_sandbox(runtime, sandbox)
        await _complete_harness_row(runtime)
        await runtime.aclose()  # teardown-pipes LAST — nothing reads a torn-down store

    result = await runtime.run_close(close_config, teardown=_ordered_tail)
    if not ran_tail:
        # Empty-ledger close: run_meeting_close returned without invoking teardown,
        # so the ordered tail is still this caller's responsibility (§3.16).
        await _ordered_tail()
    return result


async def close_meeting(db: Any, meeting_id: str, *, sandbox: Any = None) -> Any | None:
    """Claim the meeting-close unit and explicitly destroy the sandbox (legacy seam).

    The atomic-claim + explicit sandbox-destroy primitive. The full ordered close
    (freeze → close-pass → destroy-sandbox → complete-harness-row → teardown-pipes
    LAST) is :func:`run_ordered_close`, wired into the meeting-end path; this seam
    remains for the bare claim+destroy callers.
    """
    run_id = await claim_meeting(db, meeting_id, MEETING_CLOSE_OP)
    if sandbox is not None:
        await sandbox_provider.destroy(sandbox)  # explicit destroy on meeting-end
    return run_id
