"""AC-COAL-12-REAL — the drop policy must hold on the REAL micro-call chain.

The pure-simulation tier (``test_pipeline.py``) injects a fake ``scribe_call`` that
raises the *pipeline-local* typed errors, so it can pass while the production path is
broken. This test closes the 'capability only works when a test injects it' gap by
driving the REAL ``run_scribe`` consumer with the REAL ``scribe.call.scribe_call``
bound in exactly as ``harness.scribe_runtime.build_real_seams`` wires it — the ONLY
injected boundary is the vendor seam (``call_external``), which returns a real
``stop_reason == "max_tokens"`` / no-tool-use vendor response.

The real chain (``scribe_call -> parse_scribe_result``) raises
``scribe.parse.ScribeMaxTokensError`` / ``ScribeNoDeltaError``. If ``pipeline.DROP_ERRORS``
does not include the classes the real chain actually raises, the exception escapes the
consumer, ``mark_gap`` is never called, and the whole meeting's notes pipeline dies —
exactly the 'stalled meeting' §3.1/§3.2.1 forbids. This test asserts the window is
dropped (a comprehension gap recorded) and the consumer advances, on the real path.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from scribe.call import scribe_call as real_scribe_call
from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.pipeline import CallRecord, run_scribe

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scribe"))
from _fixtures import (  # noqa: E402
    FakeClient,
    FakeResp,
    TextBlock,
    ToolUseBlock,
    a_meeting,
    a_valid_delta_input,
    make_call_external,
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


async def _drive_real(vendor_resp) -> tuple[list[tuple], list[CallRecord]]:
    """Run the REAL run_scribe over one window using the REAL scribe_call.

    ``real_bound`` is the exact production binding pattern from build_real_seams: the
    real ``scribe.call.scribe_call`` closed over the meeting header + rolling summary +
    the ONE ``call_external`` seam + client. The vendor seam returns ``vendor_resp``.
    """
    header = a_meeting()
    client = FakeClient(vendor_resp)
    seam = make_call_external()

    async def real_bound(meeting_id: str, window: Window):
        return await real_scribe_call(
            header, "rolling summary so far", window, call_external=seam, client=client
        )

    gaps: list[tuple] = []
    applied: list[float] = []

    async def apply_delta(meeting_id, window, delta):
        applied.append(window.start_s)

    async def mark_gap(meeting_id, start_s, end_s, *, reason):
        gaps.append((start_s, reason))

    q: "asyncio.Queue[Window | None]" = asyncio.Queue()
    q.put_nowait(_window(0, 0.0, 10.0))
    q.put_nowait(None)

    trace: list[CallRecord] = []
    await run_scribe(
        "m-real",
        q,
        scribe_call=real_bound,
        apply_delta=apply_delta,
        mark_gap=mark_gap,
        timeout_s=30.0,
        trace=trace,
    )
    assert applied == [], "a truncated/malformed window must not apply a delta"
    return gaps, trace


def test_real_chain_max_tokens_drops_window_not_crash() -> None:
    resp = FakeResp(content=[ToolUseBlock(input=a_valid_delta_input())], stop_reason="max_tokens")
    gaps, trace = asyncio.run(_drive_real(resp))
    assert gaps == [(0.0, "ScribeMaxTokensError")], gaps
    assert trace[0].dropped is True
    assert trace[0].drop_reason == "ScribeMaxTokensError"
    assert trace[0].retries == 0


def test_real_chain_no_tool_use_drops_window_not_crash() -> None:
    resp = FakeResp(content=[TextBlock(text="I won't use the tool")], stop_reason="end_turn")
    gaps, trace = asyncio.run(_drive_real(resp))
    assert gaps == [(0.0, "ScribeNoDeltaError")], gaps
    assert trace[0].dropped is True
    assert trace[0].drop_reason == "ScribeNoDeltaError"
