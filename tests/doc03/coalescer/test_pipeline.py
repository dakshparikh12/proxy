"""AC-COAL-06,08,08-NEG,09,10,11,11-NEG,12,12-NEG,13 — the serial pipeline.

Pure simulation/static/latency tier (dependency_class: null). Drives the serial
consumer with synthetic scribe-call mocks (fast / slow / typed-error / stalling)
and asserts the observable: strict in-order processing, one call at a time per
meeting, per-host in-flight bound, the 3.5s deadline, skip-never-retry drops,
and comprehension-gap recording. No Postgres, no LLM.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.pipeline import (
    MICRO_CALL_TIMEOUT_S,
    CallRecord,
    HostBudget,
    ScribeMaxTokensError,
    ScribeNoDeltaError,
    run_scribe,
)


def _window(index: int, start: float, end: float) -> Window:
    return Window(
        segments=(
            TranscriptSegment(
                speaker=f"S{index}",
                text=f"w{index}",
                start_s=start,
                end_s=end,
                token_count=10,
            ),
        ),
        boundary_type=BoundaryType.SPEAKER_TURN,
    )


async def _drain(
    windows: list[Window],
    *,
    scribe_call,
    apply_delta,
    mark_gap,
    host_budget: HostBudget | None = None,
    timeout_s: float = MICRO_CALL_TIMEOUT_S,
    trace: list[CallRecord] | None = None,
) -> None:
    q: "asyncio.Queue[Window | None]" = asyncio.Queue()
    for w in windows:
        q.put_nowait(w)
    q.put_nowait(None)
    await run_scribe(
        "m-1",
        q,
        scribe_call=scribe_call,
        apply_delta=apply_delta,
        mark_gap=mark_gap,
        host_budget=host_budget,
        timeout_s=timeout_s,
        trace=trace,
    )


# ---------------------------------------------------------------------------
# AC-COAL-08 — windows processed strictly in order, one call at a time.
# ---------------------------------------------------------------------------
def test_coal_08_windows_processed_in_order_no_concurrency() -> None:
    windows = [_window(i, i * 10.0, i * 10.0 + 10.0) for i in range(10)]
    applied: list[int] = []
    inflight = 0
    max_inflight = 0

    async def scribe_call(meeting_id, window):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0)  # yield — a fan-out impl would interleave here
        inflight -= 1
        return {"w": window.start_s}

    async def apply_delta(meeting_id, window, delta):
        applied.append(int(window.start_s // 10))

    async def mark_gap(*a, **k):
        raise AssertionError("no drops expected")

    trace: list[CallRecord] = []
    asyncio.run(
        _drain(windows, scribe_call=scribe_call, apply_delta=apply_delta,
               mark_gap=mark_gap, trace=trace)
    )
    # THRESHOLD concurrent_calls_per_meeting_max: 1.
    assert max_inflight == 1
    # THRESHOLD out_of_order_delta_applies_allowed: 0 — deltas applied in order.
    assert applied == list(range(10))
    # window[N] completes before window[N+1] starts.
    for n in range(len(trace) - 1):
        assert trace[n].monotonic_end <= trace[n + 1].monotonic_start + 1e-9


# ---------------------------------------------------------------------------
# AC-COAL-08-NEG — no fan-out primitive on the per-meeting window queue (static).
# ---------------------------------------------------------------------------
def test_coal_08neg_no_fanout_primitive_in_pipeline_source() -> None:
    src_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "services" / "scribe" / "src" / "scribe" / "pipeline.py"
    )
    source = src_path.read_text()
    tree = ast.parse(source)
    banned_attrs = {"gather", "wait", "as_completed"}  # asyncio.<fanout>
    banned_names = {"TaskGroup", "ThreadPoolExecutor", "ProcessPoolExecutor",
                    "run_with_concurrency", "runWithConcurrency"}
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in banned_attrs:
                hits.append(fn.attr)
            if isinstance(fn, ast.Name) and fn.id in banned_names:
                hits.append(fn.id)
        if isinstance(node, ast.Attribute) and node.attr in banned_names:
            hits.append(node.attr)
    # THRESHOLD fan_out_calls_on_per_meeting_queue_allowed: 0.
    assert hits == [], f"fan-out primitive found in pipeline: {hits}"
    # And there is no create_task on the window loop either.
    assert "create_task" not in source


# ---------------------------------------------------------------------------
# AC-COAL-09 — cross-meeting ordering: earlier claim applied before later one.
# ---------------------------------------------------------------------------
def test_coal_09_earlier_window_applied_before_later_contradicting_one() -> None:
    # W1 @ T+10 carries C1; W2 @ T+40 carries C2 that contradicts C1. The serial
    # pipeline must apply C1 before C2 so C2's contradicts-link sees C1.
    w1 = _window(1, 10.0, 20.0)
    w2 = _window(2, 40.0, 50.0)
    entry_order: list[str] = []
    notes: dict[str, str] = {}

    async def scribe_call(meeting_id, window):
        return {"claim": "C1" if window.start_s == 10.0 else "C2"}

    async def apply_delta(meeting_id, window, delta):
        claim = delta["claim"]
        if claim == "C2":
            # the later claim must see the earlier antecedent already present
            assert "C1" in notes, "C2 landed before C1 — order inversion"
            notes["C2_contradicts"] = "C1"
        notes[claim] = claim
        entry_order.append(claim)

    async def mark_gap(*a, **k):
        raise AssertionError("no drops expected")

    asyncio.run(
        _drain([w1, w2], scribe_call=scribe_call, apply_delta=apply_delta,
               mark_gap=mark_gap)
    )
    assert entry_order == ["C1", "C2"]
    # THRESHOLD dangling_contradicts_links_allowed: 0.
    assert notes["C2_contradicts"] == "C1"


# ---------------------------------------------------------------------------
# AC-COAL-10 — per-host in-flight bound prevents one meeting starving others.
# ---------------------------------------------------------------------------
def test_coal_10_host_budget_bounds_inflight_across_meetings() -> None:
    budget = HostBudget(limit=2)
    inflight = 0
    max_inflight = 0
    lock = asyncio.Lock()

    async def scribe_call(meeting_id, window):
        nonlocal inflight, max_inflight
        async with lock:
            inflight += 1
            max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.01)
        async with lock:
            inflight -= 1
        return {"ok": True}

    async def apply_delta(meeting_id, window, delta):
        pass

    async def mark_gap(*a, **k):
        pass

    async def one_meeting(mid: str, n: int) -> None:
        q: "asyncio.Queue[Window | None]" = asyncio.Queue()
        for i in range(n):
            q.put_nowait(_window(i, i * 10.0, i * 10.0 + 10.0))
        q.put_nowait(None)
        await run_scribe(mid, q, scribe_call=scribe_call, apply_delta=apply_delta,
                         mark_gap=mark_gap, host_budget=budget)

    async def main() -> None:
        # one busy meeting (many windows) + several others must all progress.
        await asyncio.gather(
            one_meeting("busy", 20),
            one_meeting("m2", 3),
            one_meeting("m3", 3),
            one_meeting("m4", 3),
        )

    asyncio.run(main())
    # THRESHOLD inflight_calls_per_host_max: configured_bound (2 here).
    assert max_inflight <= 2
    # All meetings completed (none starved) — gather returned without hang.


def test_coal_10_busy_meeting_stays_serial_within_itself() -> None:
    # Even with a host budget > 1, ONE meeting still runs one call at a time.
    budget = HostBudget(limit=4)
    inflight_by_meeting: dict[str, int] = {}
    max_by_meeting: dict[str, int] = {}

    async def scribe_call(meeting_id, window):
        inflight_by_meeting[meeting_id] = inflight_by_meeting.get(meeting_id, 0) + 1
        max_by_meeting[meeting_id] = max(
            max_by_meeting.get(meeting_id, 0), inflight_by_meeting[meeting_id]
        )
        await asyncio.sleep(0.005)
        inflight_by_meeting[meeting_id] -= 1
        return {"ok": True}

    async def apply_delta(meeting_id, window, delta):
        pass

    async def mark_gap(*a, **k):
        pass

    async def one_meeting(mid: str) -> None:
        q: "asyncio.Queue[Window | None]" = asyncio.Queue()
        for i in range(5):
            q.put_nowait(_window(i, i * 10.0, i * 10.0 + 10.0))
        q.put_nowait(None)
        await run_scribe(mid, q, scribe_call=scribe_call, apply_delta=apply_delta,
                         mark_gap=mark_gap, host_budget=budget)

    async def main() -> None:
        await asyncio.gather(one_meeting("a"), one_meeting("b"))

    asyncio.run(main())
    assert max_by_meeting["a"] == 1
    assert max_by_meeting["b"] == 1


# ---------------------------------------------------------------------------
# AC-COAL-11 — each micro-call enforces a 3.5s asyncio.wait_for deadline.
# ---------------------------------------------------------------------------
def test_coal_11_stalling_call_is_cancelled_at_the_deadline() -> None:
    started = asyncio.Event() if False else None  # noqa: F841 - documentation

    async def scribe_call(meeting_id, window):
        await asyncio.sleep(100.0)  # would run unbounded without the deadline
        return {"never": True}

    gaps: list[tuple] = []

    async def apply_delta(meeting_id, window, delta):
        raise AssertionError("stalled call must not apply")

    async def mark_gap(meeting_id, start_s, end_s, *, reason):
        gaps.append((start_s, end_s, reason))

    trace: list[CallRecord] = []
    # Use a small deadline so the test is fast; the mechanism is identical.
    asyncio.run(
        _drain([_window(0, 0.0, 10.0)], scribe_call=scribe_call,
               apply_delta=apply_delta, mark_gap=mark_gap, timeout_s=0.05,
               trace=trace)
    )
    rec = trace[0]
    # THRESHOLD calls_exceeding_deadline_allowed: 0 — cancelled within deadline+jitter.
    elapsed = rec.monotonic_end - rec.monotonic_start
    assert elapsed <= 0.05 + 0.5  # deadline + generous scheduling jitter
    assert rec.dropped is True
    assert rec.drop_reason == "TimeoutError"
    assert gaps == [(0.0, 10.0, "TimeoutError")]


def test_coal_11_default_deadline_is_3_5_seconds() -> None:
    # The physics constant is exactly the spec's 3.5s.
    assert MICRO_CALL_TIMEOUT_S == 3.5


# ---------------------------------------------------------------------------
# AC-COAL-11-NEG — an on-time call (< deadline) is NOT cancelled.
# ---------------------------------------------------------------------------
def test_coal_11neg_on_time_call_completes_and_is_accepted() -> None:
    async def scribe_call(meeting_id, window):
        await asyncio.sleep(0.02)  # comfortably within the 0.1s deadline below
        return {"ok": True}

    applied: list[bool] = []

    async def apply_delta(meeting_id, window, delta):
        applied.append(True)

    async def mark_gap(*a, **k):
        raise AssertionError("on-time call must not be dropped")

    trace: list[CallRecord] = []
    asyncio.run(
        _drain([_window(0, 0.0, 10.0)], scribe_call=scribe_call,
               apply_delta=apply_delta, mark_gap=mark_gap, timeout_s=0.1,
               trace=trace)
    )
    # THRESHOLD on_time_calls_cancelled_allowed: 0.
    assert applied == [True]
    assert trace[0].dropped is False
    assert trace[0].applied is True


# ---------------------------------------------------------------------------
# AC-COAL-12 — timeout OR typed error: window dropped, pipeline advances, no retry.
# ---------------------------------------------------------------------------
def test_coal_12_timeout_and_typed_errors_drop_and_advance_no_retry() -> None:
    # w0 times out; w1 raises ScribeMaxTokensError; w2 raises ScribeNoDeltaError;
    # w3 succeeds. Each dropped window advances immediately, none is retried.
    windows = [_window(i, i * 10.0, i * 10.0 + 10.0) for i in range(4)]
    call_counts: dict[float, int] = {}

    async def scribe_call(meeting_id, window):
        call_counts[window.start_s] = call_counts.get(window.start_s, 0) + 1
        if window.start_s == 0.0:
            await asyncio.sleep(100.0)  # timeout
        if window.start_s == 10.0:
            raise ScribeMaxTokensError("truncated")
        if window.start_s == 20.0:
            raise ScribeNoDeltaError("no tool call")
        return {"ok": True}

    applied: list[float] = []

    async def apply_delta(meeting_id, window, delta):
        applied.append(window.start_s)

    gaps: list[tuple] = []

    async def mark_gap(meeting_id, start_s, end_s, *, reason):
        gaps.append((start_s, reason))

    trace: list[CallRecord] = []
    asyncio.run(
        _drain(windows, scribe_call=scribe_call, apply_delta=apply_delta,
               mark_gap=mark_gap, timeout_s=0.05, trace=trace)
    )
    # THRESHOLD retries_on_timeout_allowed: 0, retries_on_typed_error_allowed: 0.
    assert all(c == 1 for c in call_counts.values()), "no window retried"
    assert all(r.retries == 0 for r in trace)
    # dropped windows recorded a gap; the successful one applied.
    assert gaps == [(0.0, "TimeoutError"),
                    (10.0, "ScribeMaxTokensError"),
                    (20.0, "ScribeNoDeltaError")]
    assert applied == [30.0]
    # THRESHOLD next_window_blocked_after_drop_allowed: 0 — all 4 processed.
    assert [r.window_index for r in trace] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# AC-COAL-12-NEG — a successful (non-timeout, non-typed-error) result is NOT dropped.
# ---------------------------------------------------------------------------
def test_coal_12neg_successful_result_is_applied_not_dropped() -> None:
    async def scribe_call(meeting_id, window):
        return {"ok": True}

    applied: list[bool] = []

    async def apply_delta(meeting_id, window, delta):
        applied.append(True)

    async def mark_gap(*a, **k):
        raise AssertionError("successful window must not be dropped")

    trace: list[CallRecord] = []
    asyncio.run(
        _drain([_window(0, 0.0, 10.0)], scribe_call=scribe_call,
               apply_delta=apply_delta, mark_gap=mark_gap, trace=trace)
    )
    # THRESHOLD successful_windows_dropped_allowed: 0.
    assert applied == [True]
    assert trace[0].dropped is False


def test_coal_12neg_generic_exception_is_not_swallowed_as_a_drop() -> None:
    # A non-typed, non-timeout error is NOT part of the drop policy: it must
    # propagate (the drop policy excludes it), not be silently treated as a drop.
    async def scribe_call(meeting_id, window):
        raise ValueError("unexpected non-typed failure")

    async def apply_delta(meeting_id, window, delta):
        pass

    async def mark_gap(*a, **k):
        raise AssertionError("generic error is not a drop")

    raised = False
    try:
        asyncio.run(
            _drain([_window(0, 0.0, 10.0)], scribe_call=scribe_call,
                   apply_delta=apply_delta, mark_gap=mark_gap)
        )
    except ValueError:
        raised = True
    assert raised, "generic exception must not be captured by the drop policy"


# ---------------------------------------------------------------------------
# AC-COAL-13 — sustained timeouts skip-not-retry: queue bounded, pipeline advances.
# ---------------------------------------------------------------------------
def test_coal_13_sustained_timeouts_do_not_stall_the_meeting() -> None:
    # Every window's call times out; the pipeline must keep advancing (each drop
    # skips, does not retry), so notes lag does not compound and the queue drains.
    n = 30
    windows = [_window(i, i * 10.0, i * 10.0 + 10.0) for i in range(n)]
    call_counts: dict[float, int] = {}

    async def scribe_call(meeting_id, window):
        call_counts[window.start_s] = call_counts.get(window.start_s, 0) + 1
        await asyncio.sleep(100.0)  # always times out

    async def apply_delta(meeting_id, window, delta):
        raise AssertionError("no window should apply")

    gaps: list[float] = []

    async def mark_gap(meeting_id, start_s, end_s, *, reason):
        gaps.append(start_s)

    trace: list[CallRecord] = []
    asyncio.run(
        _drain(windows, scribe_call=scribe_call, apply_delta=apply_delta,
               mark_gap=mark_gap, timeout_s=0.01, trace=trace)
    )
    # THRESHOLD queue_depth_unbounded_allowed: false — every window was consumed.
    assert len(trace) == n
    # Each drop is a skip, not a retry: exactly one call per window.
    assert all(c == 1 for c in call_counts.values())
    # pipeline advance rate > 0 (all gaps recorded, in order) — never frozen.
    assert gaps == [i * 10.0 for i in range(n)]


# ---------------------------------------------------------------------------
# AC-COAL-06 — per-call latency stays flat over meeting duration / density.
# ---------------------------------------------------------------------------
def test_coal_06_per_call_latency_flat_across_duration_and_density() -> None:
    # Per-call cost is bounded by the input-window cap, not the meeting length, so
    # a constant-cost mock call yields a per-call latency that does NOT grow with
    # meeting duration or density. We measure the per-call span from the trace.
    async def scribe_call(meeting_id, window):
        await asyncio.sleep(0.002)  # constant per-call work
        return {"ok": True}

    async def apply_delta(meeting_id, window, delta):
        pass

    async def mark_gap(*a, **k):
        pass

    def p95(xs: list[float]) -> float:
        s = sorted(xs)
        idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
        return s[idx]

    percentiles: list[float] = []
    for n_windows in (60, 120, 180):  # ~30/60/90 min at 30s windows
        trace: list[CallRecord] = []
        windows = [_window(i, i * 30.0, i * 30.0 + 30.0) for i in range(n_windows)]
        asyncio.run(
            _drain(windows, scribe_call=scribe_call, apply_delta=apply_delta,
                   mark_gap=mark_gap, trace=trace)
        )
        latencies = [r.monotonic_end - r.monotonic_start for r in trace]
        percentiles.append(p95(latencies))

    # p95 within the SLO bound regardless of duration.
    assert all(p <= MICRO_CALL_TIMEOUT_S for p in percentiles)
    # No monotonic growth with meeting duration — later (longer) meetings are not
    # systematically slower per call (bounded window => flat per-call cost).
    assert max(percentiles) - min(percentiles) < 0.5
