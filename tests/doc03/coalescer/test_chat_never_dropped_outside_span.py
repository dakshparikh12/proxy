"""AC-COAL-04 correctness edge — chat is NEVER dropped, even outside any window span.

The coalescer folds each chat message into the window whose [start_s, end_s] span
contains its timestamp. But a real meeting has chat that lands where NO emitted
window covers the instant:

* chat during a pure-silence span (silence by design emits no window), and
* chat after the last emitted window's end (trailing chat, buffer already flushed).

``coalesce``'s own docstring promises chat is "never dropped as long as some window
covers that instant" — but meeting chat that lands in an uncovered gap was silently
lost from the record. These drive the REAL batch/streaming entrypoint (the same
``coalesce`` / ``Coalescer`` the harness pump uses) and assert ZERO chat is lost:
every chat message rides some emitted window, attached to the nearest one.
"""
from __future__ import annotations

from scribe.coalescer import ChatMessage, Coalescer, TranscriptSegment, coalesce


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


def _all_chat(windows: list) -> list[ChatMessage]:
    return [m for w in windows for m in w.chat_messages]


def test_chat_during_silence_gap_is_folded_into_the_nearest_window() -> None:
    # Two speech runs separated by a long silence (which emits no window). A chat
    # message lands squarely inside the silence gap — no window's span covers it.
    segments = [
        seg("A", 0.0, 4.0, 40),
        seg("", 4.0, 60.0, 0, is_speech=False),  # 56s silence — no window
        seg("A", 60.0, 64.0, 40),
    ]
    chat = [ChatMessage(sender="X", text="during silence", ts_s=30.0)]
    windows = coalesce(segments, chat)
    # THRESHOLD chat_messages_dropped_from_window_allowed: 0 — never lost.
    assert chat[0] in _all_chat(windows), "chat in a silence gap must not be dropped"
    # It attaches to some real (segment-bearing) window, not a phantom.
    for w in windows:
        assert w.segments, "no segment-less window may be emitted"


def test_chat_after_last_window_end_is_folded_into_the_trailing_window() -> None:
    # A single speech run, then chat arrives with a timestamp AFTER the last word.
    segments = [seg("A", 0.0, 5.0, 50)]
    chat = [ChatMessage(sender="Y", text="after the end", ts_s=99.0)]
    windows = coalesce(segments, chat)
    assert windows, "expected a trailing window"
    assert chat[0] in _all_chat(windows), "trailing chat must not be dropped"


def test_chat_before_first_window_start_is_folded_into_the_first_window() -> None:
    # Chat posted before anyone speaks (ts before the first segment's start).
    segments = [seg("A", 10.0, 15.0, 50)]
    chat = [ChatMessage(sender="Z", text="early", ts_s=1.0)]
    windows = coalesce(segments, chat)
    assert chat[0] in _all_chat(windows), "pre-speech chat must not be dropped"


def test_every_chat_message_rides_exactly_one_window_no_loss_no_dup() -> None:
    # A mix: in-span, gap, pre-, and post- chat across a realistic stream.
    segments = [
        seg("A", 0.0, 5.0, 50),
        seg("", 5.0, 40.0, 0, is_speech=False),  # silence gap
        seg("B", 40.0, 45.0, 50),
    ]
    chat = [
        ChatMessage(sender="a", text="pre", ts_s=-1.0),        # before first
        ChatMessage(sender="b", text="in1", ts_s=2.0),         # in window 1
        ChatMessage(sender="c", text="gap", ts_s=20.0),        # in silence gap
        ChatMessage(sender="d", text="in2", ts_s=42.0),        # in window 2
        ChatMessage(sender="e", text="post", ts_s=100.0),      # after last
    ]
    windows = coalesce(segments, chat)
    all_chat = _all_chat(windows)
    # No loss and no duplication — exactly the input set, once each.
    assert sorted(m.text for m in all_chat) == sorted(m.text for m in chat)
    assert len(all_chat) == len(set(all_chat))


def test_streaming_api_also_never_drops_trailing_chat() -> None:
    # The harness pump uses feed()/push_chat()/flush() incrementally, not coalesce().
    c = Coalescer()
    c.push_chat(ChatMessage(sender="Y", text="trailing", ts_s=99.0))
    windows: list = []
    for s in [seg("A", 0.0, 5.0, 50)]:
        windows.extend(c.feed(s))
    windows.extend(c.flush())
    assert any(
        m.text == "trailing" for w in windows for m in w.chat_messages
    ), "streaming flush must not drop trailing chat"
