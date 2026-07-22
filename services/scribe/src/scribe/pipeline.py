"""The serial pipeline — one consumer per meeting; ordering is correctness.

The Scribe processes windows **strictly in order, one micro-call at a time per
meeting** (03-MEETING-UNDERSTANDING §3.1). We deliberately do NOT fan out per
meeting: parallel windows would let a claim from T+40s land before its T+10s
antecedent and corrupt ``contradicts`` links / firmness progression. The real
concurrency axis is *many meetings per host* — bounded by a per-host in-flight
semaphore so one busy meeting cannot starve another — but each meeting's consumer
is a single serial loop over its own window queue.

Per-call policy (§3.1, §3.2.1):

* Each micro-call runs under a ``[~3.5s]`` deadline via ``asyncio.wait_for``.
* On timeout — or on either typed error (``ScribeMaxTokensError`` /
  ``ScribeNoDeltaError``) — the window is **dropped and the pipeline advances**;
  it is never retried. A dropped note is fine (transcript is preserved, content
  usually recurs, the close pass backfills); a stalled meeting is not.
* A dropped span is recorded as a **comprehension gap** on the transcript plane,
  never silently missing.
* A successful call's delta is applied (the applier folds it into the one notes
  object) transactionally with the ``pending → comprehended`` flip.

The Postgres-touching seams (gap recording, transactional apply, re-claim) are
expressed as injected protocols so the *pure* ordering/timeout/drop logic is fully
simulation-testable; the real Postgres transaction (delta append + segment flip in
ONE tx) is exercised at the integration tier.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from .coalescer import Window

# --- Physics constant: the per-call deadline (§3.1). Only physics live in code. ---
MICRO_CALL_TIMEOUT_S: float = 3.5
"""Per-micro-call deadline enforced by ``asyncio.wait_for`` (§3.1)."""


# ---- Typed errors (§3.2.1). Both are non-retryable at the window level. ----
class ScribeMaxTokensError(Exception):
    """stop_reason == 'max_tokens': window/output too big — skip this window."""


class ScribeNoDeltaError(Exception):
    """Model didn't call the tool — malformed turn, skip this window."""


# The exact set of drop-triggering exceptions (§3.1). A result that is NOT one of
# these is accepted and applied — the positive boundary of the drop policy.
DROP_ERRORS: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    ScribeMaxTokensError,
    ScribeNoDeltaError,
)
# The drop reason recorded on the gap is ``type(e).__name__`` (§3.1) — a plain
# string; no enum is needed.


@dataclass(frozen=True)
class CallRecord:
    """Observability trace for one window's processing — the simulation oracle reads this."""

    window_index: int
    start_s: float
    end_s: float
    monotonic_start: float
    monotonic_end: float
    dropped: bool
    drop_reason: str | None
    applied: bool
    retries: int  # always 0 — skip-never-retry (§3.1)


class ScribeCaller(Protocol):
    """The one micro-call: window -> delta. Raises typed errors from §3.2.1."""

    async def __call__(self, meeting_id: str, window: Window) -> object: ...


class DeltaApplier(Protocol):
    """Folds a successful delta into the notes object, transactionally with the flip.

    In production this is the ``apply_delta`` seam that runs the note-delta append
    AND the ``pending → comprehended`` transcript flip in ONE Postgres transaction
    (§3.1 transactional coupling / §12.10). The pipeline calls it exactly once per
    successful window, serially, so single-writer-per-meeting holds structurally.
    """

    async def __call__(
        self, meeting_id: str, window: Window, delta: object
    ) -> None: ...


class GapRecorder(Protocol):
    """Records a dropped span as an explicit comprehension gap (§3.1, §3.3)."""

    async def __call__(
        self, meeting_id: str, start_s: float, end_s: float, *, reason: str
    ) -> None: ...


@dataclass
class HostBudget:
    """Per-host in-flight LLM-call bound so one meeting can't starve another (§3.1).

    A single asyncio semaphore shared by every meeting's consumer on the host caps
    total concurrent micro-calls. Each meeting still runs its own serial loop; the
    budget only gates the *shared* resource (LLM slots), never the per-meeting order.
    """

    limit: int
    _sem: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("host in-flight limit must be >= 1")
        self._sem = asyncio.Semaphore(self.limit)

    def slot(self) -> asyncio.Semaphore:
        return self._sem


async def run_scribe(
    meeting_id: str,
    windows: "asyncio.Queue[Window | None]",
    *,
    scribe_call: ScribeCaller,
    apply_delta: DeltaApplier,
    mark_gap: GapRecorder,
    host_budget: HostBudget | None = None,
    timeout_s: float = MICRO_CALL_TIMEOUT_S,
    trace: list[CallRecord] | None = None,
) -> None:
    """One serial consumer for ONE meeting's window queue (§3.1).

    Consumes windows strictly in order — window[N] completes (applied or dropped)
    before window[N+1] starts — until a ``None`` sentinel is dequeued. Each call
    runs under ``asyncio.wait_for(timeout_s)``; on timeout or a typed error the
    window is dropped, its span recorded as a comprehension gap, and the loop
    advances WITHOUT retry. There is no fan-out here: no ``gather``/task-group/
    concurrency primitive is applied across this meeting's windows.
    """
    index = 0
    while (window := await windows.get()) is not None:
        record = await _process_one(
            meeting_id,
            window,
            index,
            scribe_call=scribe_call,
            apply_delta=apply_delta,
            mark_gap=mark_gap,
            host_budget=host_budget,
            timeout_s=timeout_s,
        )
        if trace is not None:
            trace.append(record)
        index += 1


async def _process_one(
    meeting_id: str,
    window: Window,
    index: int,
    *,
    scribe_call: ScribeCaller,
    apply_delta: DeltaApplier,
    mark_gap: GapRecorder,
    host_budget: HostBudget | None,
    timeout_s: float,
) -> CallRecord:
    """Process exactly one window: call, then apply-or-drop. Never raises."""
    loop = asyncio.get_event_loop()
    mono_start = loop.time()

    async def _call() -> object:
        # Gate on the shared per-host LLM budget only around the actual call, so
        # queued windows wait for a slot rather than pre-empting other meetings.
        if host_budget is not None:
            async with host_budget.slot():
                return await asyncio.wait_for(
                    scribe_call(meeting_id, window), timeout=timeout_s
                )
        return await asyncio.wait_for(
            scribe_call(meeting_id, window), timeout=timeout_s
        )

    try:
        delta = await _call()
    except DROP_ERRORS as e:  # timeout OR either typed error → drop, no retry
        reason = type(e).__name__
        await mark_gap(
            meeting_id, window.start_s, window.end_s, reason=reason
        )
        return CallRecord(
            window_index=index,
            start_s=window.start_s,
            end_s=window.end_s,
            monotonic_start=mono_start,
            monotonic_end=loop.time(),
            dropped=True,
            drop_reason=reason,
            applied=False,
            retries=0,
        )

    # Success: not a timeout, not a typed error → accepted and applied (§3.1).
    await apply_delta(meeting_id, window, delta)
    return CallRecord(
        window_index=index,
        start_s=window.start_s,
        end_s=window.end_s,
        monotonic_start=mono_start,
        monotonic_end=loop.time(),
        dropped=False,
        drop_reason=None,
        applied=True,
        retries=0,
    )
