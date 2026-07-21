"""Rolling summary: delta-count trigger, time trigger, off-hot-path refresh.

Covers AC-SCRIBE-05, -06, -07. Time is injected (now_s) so triggers fire one at a
time with a frozen clock.
"""
from __future__ import annotations

import asyncio

from scribe.rolling_summary import (
    ROLLING_SUMMARY_PROMPT,
    SummaryState,
    build_summary_request,
    maybe_refresh_in_background,
    regenerate_rolling_summary,
    rolling_summary_due,
    summary_model,
)

from _fixtures import FakeClient, FakeResp, TextBlock, make_call_external


def _run(coro):
    # Own a fresh loop per call so the test does not depend on (or mutate) the
    # process-wide current loop — deterministic and free of the 3.12
    # get_event_loop() deprecation.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_scribe_05_delta_count_trigger_fires_once_at_20() -> None:
    st = SummaryState(last_refresh_s=0.0)
    refreshes = 0
    for i in range(1, 22):
        st.note_delta_applied()
        if rolling_summary_due(st, now_s=0.0, every_n_deltas=20, max_age_s=90.0):
            refreshes += 1
            st.mark_refreshed(0.0)
            assert i == 20, f"refresh fired at delta {i}, expected 20"
    assert refreshes == 1


def test_scribe_05_no_refresh_before_threshold() -> None:
    st = SummaryState(last_refresh_s=0.0)
    for _ in range(19):
        st.note_delta_applied()
    assert rolling_summary_due(st, now_s=0.0, every_n_deltas=20, max_age_s=90.0) is False


def test_scribe_05_counter_resets_after_refresh() -> None:
    st = SummaryState(last_refresh_s=0.0)
    for _ in range(20):
        st.note_delta_applied()
    assert rolling_summary_due(st, now_s=0.0, every_n_deltas=20, max_age_s=90.0) is True
    st.mark_refreshed(0.0)
    assert st.deltas_since == 0
    st.note_delta_applied()
    assert rolling_summary_due(st, now_s=0.0, every_n_deltas=20, max_age_s=90.0) is False


def test_scribe_06_time_trigger_fires_at_90s_not_before() -> None:
    st = SummaryState(last_refresh_s=0.0)
    st.deltas_since = 0
    assert rolling_summary_due(st, now_s=89.0, every_n_deltas=20, max_age_s=90.0) is False
    assert rolling_summary_due(st, now_s=91.0, every_n_deltas=20, max_age_s=90.0) is True


def test_scribe_06_exactly_one_time_refresh_with_deltas_held_low() -> None:
    st = SummaryState(last_refresh_s=0.0)
    st.deltas_since = 5
    fires = 0
    for t in (30.0, 60.0, 89.0, 91.0, 120.0):
        if rolling_summary_due(st, now_s=t, every_n_deltas=20, max_age_s=90.0):
            fires += 1
            st.mark_refreshed(t)
    assert fires == 1


def test_scribe_07_refresh_does_not_block_the_serial_consumer() -> None:
    async def scenario() -> None:
        st = SummaryState(last_refresh_s=0.0, deltas_since=20)
        refresh_started = asyncio.Event()
        refresh_done = asyncio.Event()
        progress: list[str] = []

        async def slow_refresh() -> None:
            refresh_started.set()
            await asyncio.sleep(0.2)
            refresh_done.set()

        task = maybe_refresh_in_background(st, slow_refresh, now_s=0.0, every_n_deltas=20, max_age_s=90.0)
        assert task is not None
        await refresh_started.wait()
        assert not refresh_done.is_set()
        progress.append("next_window_processed")
        assert progress == ["next_window_processed"]
        assert st.deltas_since == 0
        assert rolling_summary_due(st, now_s=0.0, every_n_deltas=20, max_age_s=90.0) is False
        await task
        assert refresh_done.is_set()

    _run(scenario())


def test_scribe_07_no_refresh_scheduled_when_not_due() -> None:
    st = SummaryState(last_refresh_s=0.0, deltas_since=0)

    async def _r() -> None:
        raise AssertionError("refresh should not have been scheduled")

    assert maybe_refresh_in_background(st, _r, now_s=1.0, every_n_deltas=20, max_age_s=90.0) is None


def test_regenerate_rolling_summary_one_call_returns_text(monkeypatch) -> None:
    monkeypatch.delenv("PROXY_MODEL_SCRIBE", raising=False)
    client = FakeClient(FakeResp(content=[TextBlock(text="  compact summary  ")]))
    out = _run(
        regenerate_rolling_summary("notes rendered", call_external=make_call_external(), client=client)
    )
    assert out == "compact summary"
    assert len(client.messages.calls) == 1
    req = client.messages.calls[0]
    assert req["system"] == ROLLING_SUMMARY_PROMPT
    assert req["model"] == "claude-haiku-4-5"
    assert "tools" not in req


def test_build_summary_request_reads_notes_not_transcript(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_MODEL_SCRIBE", "claude-haiku-4-5")
    req = build_summary_request("NOTES OBJECT RENDER")
    assert req["messages"][0]["content"] == "NOTES OBJECT RENDER"
    assert summary_model() == "claude-haiku-4-5"
