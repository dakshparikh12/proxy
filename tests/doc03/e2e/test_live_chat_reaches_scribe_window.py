"""AC-COAL-04 (live path) — inbound meeting chat reaches the Scribe window, not the floor.

Gap DOC03-LIVE-CHAT-DROPPED-IN-PUMP: ``transport.chat.ChatChannel.dispatch_inbound``
emits every inbound chat line as a ``ChatMessage`` signal onto the SAME
``SignalCarrier`` the Scribe pump subscribes to (chat.py: ``await self._carrier.emit(msg)``).
But ``control_plane.scribe_runtime._pump_transcripts`` only handled ``Transcript`` and
``MeetingEnd`` signals — a live ``ChatMessage`` fell through and was DISCARDED. It was
never handed to ``coalescer.push_chat``, so it never folded into any window and never
reached the Scribe or the notes. The coalescer's chat-folding machinery only functioned
in the batch ``coalesce()`` wrapper (tests); on the live path the "chat never dropped"
guarantee was a test-only capability.

This drives the REAL production assembly — ``launch_scribe_runtime`` (the ONE production
caller of ``run_scribe``) over a real ``SignalCarrier`` — and emits the chat line through
the REAL ``ChatChannel.dispatch_inbound`` emit end (no manual ``push_chat`` anywhere). The
``scribe_call`` seam is the recordable vendor boundary (never a product double); it records
each window it is handed. The assertion: the window the Scribe was called with carries the
inbound chat message. If the pump drops chat (no ``ChatMessage`` branch), no window carries
it and this fails.

AC-COAL-04: "Each window carries speakers, timestamps, and chat messages from the same
span" — ``chat_messages_dropped_from_window_allowed: 0``.
"""
from __future__ import annotations

import asyncio

import pytest

from scribe.coalescer import Window
from scribe.prefix import MeetingHeader
from transport.carrier import SignalCarrier
from transport.chat import ChatChannel
from transport.signals import ChatMessage, MeetingEnd, Transcript

from control_plane.scribe_runtime import launch_scribe_runtime


class _StubTransport:
    """A no-op transport: the inbound path never touches it (only outbound would)."""


@pytest.mark.asyncio
async def test_live_inbound_chat_lands_in_scribe_window() -> None:
    carrier = SignalCarrier()
    header = MeetingHeader(meeting_id="m-chat-live", participants=("Ana", "Zed"))

    # The recordable vendor seam: capture every window handed to the Scribe.
    seen_windows: list[Window] = []

    async def recording_scribe_call(meeting_id: str, window: Window) -> object:
        seen_windows.append(window)
        # An op-less delta: noop_apply reads .ops via getattr -> [] (a no-op).
        return object()

    async def noop_apply(meeting_id: str, window: Window, delta: object) -> None:
        return None

    async def noop_gap(meeting_id: str, start_s: float, end_s: float, *, reason: str) -> None:
        return None

    handle = launch_scribe_runtime(
        header,
        carrier,
        scribe_call=recording_scribe_call,
        apply_delta=noop_apply,
        mark_gap=noop_gap,
    )

    # The REAL inbound chat emit end — ChatChannel emits onto the SAME carrier.
    chat = ChatChannel(
        _StubTransport(),
        carrier,
        bot_id="bot-1",
        ask_sink=lambda ask: None,
        degrade_hook=lambda req: None,
    )

    # Two real transcript words spanning a window, with a chat line landing in-span.
    await carrier.emit(Transcript(words="First item is the retry backoff.", speaker="Ana", t=0.0))
    await chat.dispatch_inbound(
        ChatMessage(message="+1 to the retry backoff", sender="Zed")
    )
    await carrier.emit(Transcript(words="Agreed, ship it today.", speaker="Ana", t=5.0))
    await carrier.emit(MeetingEnd(reason="call_ended"))

    await asyncio.wait_for(handle.wait(), timeout=5.0)

    all_chat = [c for w in seen_windows for c in w.chat_messages]
    assert any(c.text == "+1 to the retry backoff" and c.sender == "Zed" for c in all_chat), (
        "inbound meeting chat was dropped on the live path — no Scribe window carried it "
        f"(windows chat = {all_chat!r}); the pump has no ChatMessage branch (AC-COAL-04)"
    )
