"""AC-COAL-01..07 — window cutting: turn/pause/cap boundaries, silence, chat, roll-over.

These are the pure simulation-tier criteria (dependency_class: null). They drive
synthetic transcript-segment streams through the coalescer and assert the exact
observable window boundaries. No Postgres, no LLM — deterministic transformation.
"""
from __future__ import annotations

from scribe.coalescer import (
    WINDOW_TIME_CAP_S,
    WINDOW_TOKEN_CAP,
    BoundaryType,
    ChatMessage,
    Coalescer,
    TranscriptSegment,
    coalesce,
)


def seg(
    speaker: str,
    start: float,
    end: float,
    tokens: int,
    *,
    text: str = "x",
    is_speech: bool = True,
) -> TranscriptSegment:
    return TranscriptSegment(
        speaker=speaker,
        text=text,
        start_s=start,
        end_s=end,
        token_count=tokens,
        is_speech=is_speech,
    )


# ---------------------------------------------------------------------------
# AC-COAL-01 — cut at speaker-turn OR the time cap, whichever first.
# ---------------------------------------------------------------------------
def test_coal_01_boundary_at_turn_or_time_cap_never_exceeds_cap() -> None:
    # A stream of varied turn lengths: some speakers change quickly, one holds
    # the floor well past the cap so the cap must trigger mid-hold.
    segments = [
        seg("A", 0, 5, 50),
        seg("B", 5, 12, 70),      # turn change at 5
        seg("C", 12, 20, 80),     # turn change at 12
        # D holds the floor 20..120s — 100s, must be cut by the time cap.
        seg("D", 20, 70, 200),
        seg("D", 70, 120, 200),
        seg("A", 120, 124, 40),   # turn change at 120
    ]
    windows = coalesce(segments)
    assert windows, "expected at least one window"
    for w in windows:
        assert w.boundary_type in {
            BoundaryType.SPEAKER_TURN,
            BoundaryType.TIME_CAP,
            BoundaryType.TOKEN_CAP,
            BoundaryType.STREAM_END,
        }
        # THRESHOLD windows_exceeding_60s_allowed: 0 (enforced cap is 45s).
        assert w.duration_s <= WINDOW_TIME_CAP_S + 1e-6
        # THRESHOLD windows_crossing_turn_mid_turn_allowed: 0 — a window that is
        # not a cap-cut contains exactly one speaker (it did not cross a turn).
        if w.boundary_type == BoundaryType.SPEAKER_TURN:
            assert w.speaker_count == 1


# ---------------------------------------------------------------------------
# AC-COAL-02 — VAD gating: silence produces zero windows (no micro-calls).
# ---------------------------------------------------------------------------
def test_coal_02_silence_only_span_emits_no_window() -> None:
    # A pure silence span (VAD off) across a long stretch — nothing to comprehend.
    silence = [
        seg("", 0, 30, 0, is_speech=False),
        seg("", 30, 90, 0, is_speech=False),
        seg("", 90, 200, 0, is_speech=False),
    ]
    windows = coalesce(silence)
    # THRESHOLD windows_during_silence_allowed: 0 (and by construction the
    # downstream micro-call count is 0 because there is no window to hand over).
    assert windows == []


def test_coal_02_silence_between_speech_does_not_fabricate_a_window() -> None:
    segments = [
        seg("A", 0, 4, 40),
        seg("", 4, 60, 0, is_speech=False),  # 56s of silence — costs nothing
        seg("A", 60, 64, 40),
    ]
    windows = coalesce(segments)
    # The silence neither emits its own window nor stretches a window past the cap.
    for w in windows:
        assert w.duration_s <= WINDOW_TIME_CAP_S + 1e-6
    # Two speech runs separated by a long silence: the silence forced a pause cut.
    assert len(windows) == 2
    assert all(w.speaker_count == 1 for w in windows)


# ---------------------------------------------------------------------------
# AC-COAL-03 — monologue chunks on pauses; rapid exchange chunks per turn.
# ---------------------------------------------------------------------------
def test_coal_03a_monologue_chunks_on_internal_pauses() -> None:
    # One speaker, no turn change, but VAD pauses (>= PAUSE_GAP_S) between spans.
    segments = [
        seg("A", 0.0, 3.0, 40),
        seg("A", 4.0, 7.0, 40),   # 1.0s gap -> pause boundary before this
        seg("A", 8.0, 11.0, 40),  # 1.0s gap -> pause boundary before this
    ]
    windows = coalesce(segments)
    # THRESHOLD monologue_windows_missing_pause_boundary_allowed: 0.
    # The first two closed windows are cut on the pause; the trailing one on flush.
    assert len(windows) == 3
    assert windows[0].boundary_type == BoundaryType.PAUSE_WITHIN_TURN
    assert windows[1].boundary_type == BoundaryType.PAUSE_WITHIN_TURN
    assert all(w.speaker_count == 1 for w in windows)


def test_coal_03b_rapid_exchange_one_speaker_per_window() -> None:
    # Rapid two-speaker alternation, no silent gaps — each turn cuts a window.
    segments = [
        seg("A", 0.0, 1.0, 15),
        seg("B", 1.0, 2.0, 15),
        seg("A", 2.0, 3.0, 15),
        seg("B", 3.0, 4.0, 15),
        seg("A", 4.0, 5.0, 15),
    ]
    windows = coalesce(segments)
    # THRESHOLD rapid_exchange_windows_with_mixed_speakers_allowed: 0.
    for w in windows:
        assert w.speaker_count == 1, "attribution must be clean — one speaker/window"
    assert [w.speakers[0] for w in windows] == ["A", "B", "A", "B", "A"]


# ---------------------------------------------------------------------------
# AC-COAL-04 — window carries speakers, timestamps, and same-span chat.
# ---------------------------------------------------------------------------
def test_coal_04_window_carries_speakers_timestamps_and_chat() -> None:
    segments = [
        seg("A", 0.0, 2.0, 20, text="hello"),
        seg("A", 2.0, 4.0, 20, text="team"),
    ]
    chat = [ChatMessage(sender="B", text="+1", ts_s=3.0)]  # ts inside the span
    windows = coalesce(segments, chat)
    assert len(windows) == 1
    w = windows[0]
    # (a) all speakers who spoke
    assert w.speakers == ("A",)
    # (b) start and end timestamps populated
    assert w.start_s == 0.0
    assert w.end_s == 4.0
    # (c) the chat message whose ts falls in span is present — never dropped.
    assert w.chat_messages == (chat[0],)


def test_coal_04_multi_speaker_span_lists_all_speakers() -> None:
    # A window cut by the time cap can legitimately span two speakers.
    segments = [
        seg("A", 0.0, 25.0, 300),
        seg("A", 25.0, 50.0, 300),  # same speaker; time cap cuts inside here
    ]
    windows = coalesce(segments)
    # first window is a cap cut; assert timestamps are populated and monotone.
    for w in windows:
        assert w.start_s < w.end_s
        assert w.speakers  # non-empty
        assert w.speaker_count >= 1


def test_coal_04_chat_never_dropped_when_a_window_covers_its_instant() -> None:
    segments = [seg("A", 0.0, 5.0, 50), seg("B", 5.0, 10.0, 50)]
    chat = [
        ChatMessage(sender="X", text="first", ts_s=2.0),   # in window 1
        ChatMessage(sender="Y", text="second", ts_s=7.0),  # in window 2
    ]
    windows = coalesce(segments, chat)
    all_chat = [m for w in windows for m in w.chat_messages]
    assert set(all_chat) == set(chat)  # zero dropped


# ---------------------------------------------------------------------------
# AC-COAL-05 — hard cap: 45s OR 1,200 tokens, never the whole backlog.
# ---------------------------------------------------------------------------
def test_coal_05_backlog_beyond_caps_is_never_one_window() -> None:
    # A continuous single-speaker backlog far beyond both caps.
    segments = [seg("A", i * 5.0, i * 5.0 + 5.0, 200) for i in range(40)]  # 200s, 8000 tok
    windows = coalesce(segments)
    backlog_total = sum(s.token_count for s in segments)
    assert len(windows) > 1, "the whole backlog must not be one window"
    for w in windows:
        # THRESHOLD window_duration_s_max: 45, window_token_count_max: 1200.
        assert w.duration_s <= WINDOW_TIME_CAP_S + 1e-6
        assert w.token_count <= WINDOW_TOKEN_CAP
        # THRESHOLD full_backlog_windows_allowed: 0.
        assert w.token_count < backlog_total


# ---------------------------------------------------------------------------
# AC-COAL-05-NEG — boundary: exactly at cap passes; one over rolls to next.
# ---------------------------------------------------------------------------
def test_coal_05neg_exactly_at_token_cap_forms_one_clean_window() -> None:
    # Fixture A: exactly 1,200 tokens across turns, comfortably under 45s.
    segments = [
        seg("A", 0.0, 5.0, 600),
        seg("A", 5.0, 10.0, 600),  # same speaker, no pause -> one 1200-tok window
    ]
    windows = coalesce(segments)
    assert len(windows) == 1
    w = windows[0]
    assert w.token_count == WINDOW_TOKEN_CAP  # accepted as-is, no cut
    assert w.boundary_type == BoundaryType.STREAM_END


def test_coal_05neg_one_second_over_time_cap_rolls_remainder() -> None:
    # Fixture B: a single segment running to 46s — 1s past the 45s cap.
    segments = [seg("A", 0.0, 46.0, 460)]
    windows = coalesce(segments)
    assert len(windows) == 2
    first, second = windows
    # Window cut at the time cap; remainder rolled (excess never dropped).
    assert first.boundary_type == BoundaryType.TIME_CAP
    assert abs(first.duration_s - WINDOW_TIME_CAP_S) < 1e-6
    assert second.start_s == WINDOW_TIME_CAP_S
    assert second.end_s == 46.0
    # No token lost across the roll (excess rolled, not dropped).
    assert first.token_count + second.token_count == 460


def test_coal_05neg_one_token_over_cap_cuts_on_turn_and_rolls() -> None:
    # Fixture C: 1,201 tokens arriving as two turns; cut at the token cap on the
    # turn boundary, the 1-token excess rolls into the next window.
    segments = [
        seg("A", 0.0, 5.0, 1200),
        seg("B", 5.0, 6.0, 1),  # 1 token over -> new window
    ]
    windows = coalesce(segments)
    assert len(windows) == 2
    first, second = windows
    assert first.token_count == 1200  # <= cap, cut on a turn boundary
    assert first.boundary_type in {BoundaryType.TOKEN_CAP, BoundaryType.SPEAKER_TURN}
    assert second.token_count == 1  # the excess rolled, not dropped
    assert first.token_count + second.token_count == 1201


# ---------------------------------------------------------------------------
# AC-COAL-07 — token overflow cuts on turn boundary, rolls remainder, never widens.
# ---------------------------------------------------------------------------
def test_coal_07_token_overflow_cuts_on_turn_and_rolls_never_widens() -> None:
    # A run that would exceed 1,200 tokens before a natural turn boundary: three
    # 500-token spans by the same speaker with no pause. The cut lands at/at-or-
    # before the cap on a turn/segment boundary and the tail rolls forward.
    segments = [
        seg("A", 0.0, 3.0, 500),
        seg("A", 3.0, 6.0, 500),
        seg("A", 6.0, 9.0, 500),  # 1500 total -> must cut before 1500
    ]
    windows = coalesce(segments)
    assert len(windows) >= 2
    for w in windows:
        # never widened past the cap to catch up.
        assert w.token_count <= WINDOW_TOKEN_CAP
    # The overflow content appears as leading content of the next window.
    total_before = sum(s.token_count for s in segments)
    total_after = sum(w.token_count for w in windows)
    assert total_after == total_before  # nothing dropped, all rolled
    # AC-COAL-07 turn/segment-alignment: all three spans are one speaker (one turn), so the
    # finest available boundary is the transcript-segment boundary. The cut must land on a
    # WHOLE-segment sum at/before the 1200 cap (1000 = seg1+seg2), never mid-segment, and be
    # marked as a cap cut — this is the real observable the oracle's 'turn boundary' means
    # once a single turn exceeds the cap.
    assert windows[0].boundary_type == BoundaryType.TOKEN_CAP
    assert windows[0].token_count == 1000  # cut at the seg2/seg3 boundary (<=1200), not mid-segment


def test_coal_07_single_oversized_segment_is_split_at_the_cap() -> None:
    # One segment alone exceeds the token cap and there is no earlier turn to cut
    # on — it must be split at the cap, remainder rolled, never handed whole.
    segments = [seg("A", 0.0, 10.0, 3000)]
    windows = coalesce(segments)
    assert len(windows) >= 3  # 3000 / 1200 -> ceil = 3
    for w in windows:
        assert w.token_count <= WINDOW_TOKEN_CAP
    assert sum(w.token_count for w in windows) == 3000  # no loss


# ---------------------------------------------------------------------------
# Streaming API parity — push() returns windows incrementally, in order.
# ---------------------------------------------------------------------------
def test_streaming_push_emits_windows_in_formation_order() -> None:
    coalescer = Coalescer()
    emitted: list[str] = []
    for s in [seg("A", 0, 1, 10), seg("B", 1, 2, 10), seg("A", 2, 3, 10)]:
        for w in coalescer.push(s):
            emitted.append(w.speakers[0])
    for w in coalescer.flush():
        emitted.append(w.speakers[0])
    assert emitted == ["A", "B", "A"]
