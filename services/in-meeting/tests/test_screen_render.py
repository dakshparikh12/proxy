"""SCREEN capability — the real render path (Law 2: no silent no-op reported as success).

Before this fix ``set_screen(url)`` enqueued ``{"screen": url}`` but the page dropped the frame
(no render branch), and the sink returned a fabricated success. These prove the whole path is real:

  * a URL value → a ``{"screen": url}`` render frame lands on the channel;
  * CONTENT (raw html/text/markdown) → a ``{"screen_html": <wrapped html>}`` frame lands;
  * an oversize html frame → an honest error, nothing enqueued;
  * clearing → a ``{"screen": ""}`` frame the page reads as "back to the orb";
  * the page JS actually has the render branch (iframe swap + srcdoc);
  * the ``_screen`` sink and ``to_meeting('screen')`` return what ACTUALLY happened.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


def test_url_value_enqueues_a_screen_render_frame() -> None:
    """A http(s) URL → a ``{"screen": url}`` frame; ``show_screen`` reports it honestly."""
    from in_meeting.output_media import OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-screen-url")
        result = await ch.show_screen("https://example.com/report.html")
        assert result.rendered is True
        assert result.kind == "url"
        frames = [f for f in ch._frames if isinstance(f, str) and "screen" in f]
        payloads = [json.loads(f) for f in frames]
        assert {"screen": "https://example.com/report.html"} in payloads
        assert ch.screen_url() == "https://example.com/report.html"

    asyncio.run(_run())


def test_content_value_enqueues_a_wrapped_screen_html_frame() -> None:
    """Bare text/markdown (not a URL) → a ``{"screen_html": ...}`` frame wrapped in a minimal,
    self-contained HTML shell (srcdoc always renders — no X-Frame-Options risk)."""
    from in_meeting.output_media import OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-screen-content")
        result = await ch.show_screen("# Plan\nStep one, then step two.")
        assert result.rendered is True
        assert result.kind == "content"
        html_frames = [
            json.loads(f)["screen_html"]
            for f in ch._frames
            if isinstance(f, str) and "screen_html" in f
        ]
        assert len(html_frames) == 1
        html = html_frames[0]
        # A real, self-contained shell (dark bg, monospace-friendly) wrapping the content.
        assert "<html" in html.lower()
        assert "Step one, then step two." in html
        # Bare content is not treated as a URL surface.
        assert ch.screen_url() == ""

    asyncio.run(_run())


def test_raw_html_content_is_passed_through_the_shell() -> None:
    """Content that already looks like HTML still rides srcdoc as a self-contained document."""
    from in_meeting.output_media import OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-screen-html")
        result = await ch.show_screen("<h1>Hi</h1><p>body</p>")
        assert result.rendered is True and result.kind == "content"
        html = next(
            json.loads(f)["screen_html"]
            for f in ch._frames
            if isinstance(f, str) and "screen_html" in f
        )
        assert "<h1>Hi</h1>" in html

    asyncio.run(_run())


def test_oversize_content_is_an_honest_error_and_enqueues_nothing() -> None:
    """A content frame past the size cap → honest failure; no screen frame is enqueued (Law 2)."""
    from in_meeting.output_media import MAX_SCREEN_HTML_BYTES, OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-screen-big")
        huge = "x" * (MAX_SCREEN_HTML_BYTES + 10_000)
        result = await ch.show_screen(huge)
        assert result.rendered is False
        assert "too large" in result.detail.lower() or "exceeds" in result.detail.lower()
        assert not [f for f in ch._frames if isinstance(f, str) and "screen" in f]

    asyncio.run(_run())


def test_clear_screen_enqueues_a_back_to_orb_frame() -> None:
    """Empty value → a ``{"screen": ""}`` frame the page reads as "back to the orb"."""
    from in_meeting.output_media import OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-screen-clear")
        await ch.show_screen("https://example.com/x.html")
        ch._frames.clear()
        result = await ch.show_screen("")
        assert result.rendered is True and result.kind == "clear"
        cleared = [
            json.loads(f)
            for f in ch._frames
            if isinstance(f, str) and "screen" in f
        ]
        assert {"screen": ""} in cleared
        assert ch.screen_url() == ""

    asyncio.run(_run())


def test_show_screen_reports_no_page_attached_but_still_ok() -> None:
    """With no page attached the frame still rides (delivers on reconnect) — reported honestly:
    rendered True but detail names that no page is currently connected."""
    from in_meeting.output_media import OutputMediaChannel

    async def _run() -> None:
        ch = OutputMediaChannel("m-screen-nopage")
        result = await ch.show_screen("https://example.com/x.html")
        assert result.rendered is True
        assert ch.connected() is False
        assert "no page" in result.detail.lower() or "reconnect" in result.detail.lower()

    asyncio.run(_run())


def test_page_js_has_the_screen_render_branch() -> None:
    """The inline page JS must handle screen frames: a URL swaps the orb for a sandboxed iframe,
    raw html rides srcdoc, and an empty value returns to the orb. Assert the shipped JS so the
    client half can't silently regress (mirrors the barge-in JS-assertion pattern)."""
    from in_meeting.output_media import _render_page

    page = _render_page("m-page-screen")
    # A render branch keyed on both frame shapes:
    assert '"screen"' in page or "'screen'" in page
    assert "screen_html" in page
    # It renders via a sandboxed iframe and uses srcdoc for self-contained content:
    assert "iframe" in page.lower()
    assert "sandbox" in page.lower()
    assert "srcdoc" in page
    # Clearing returns to the orb (the orb code is kept intact).
    assert "orb" in page


def test_screen_sink_returns_honest_outcome() -> None:
    """The provisioner ``_screen`` sink returns an honest human-readable outcome string reflecting
    what actually happened (showing <url|content>), not a fabricated success."""
    import in_meeting.output_media as om
    from control_plane.provisioner import _build_meeting_sinks

    class _DB:  # minimal stand-in; the screen sink never touches the db
        pass

    async def _run() -> None:
        _offer, screen, _mute = _build_meeting_sinks(
            db=_DB(), meeting_id="m-sink-screen", tenant_id="t1"
        )
        out = await screen("https://example.com/r.html")
        assert "showing" in out.lower()
        assert "https://example.com/r.html" in out
        # A real frame landed on the real channel (honest — not fabricated).
        ch = om.channel_for("m-sink-screen")
        assert any(
            isinstance(f, str) and "screen" in f for f in ch._frames
        )
        om.close_channel("m-sink-screen")

    asyncio.run(_run())


def test_to_meeting_screen_returns_honest_detail() -> None:
    """``to_meeting(medium='screen')`` returns ``ok=True`` only when the sink reports a real render,
    and surfaces the honest detail (not a bare fabricated url)."""
    from in_meeting.meeting_connection import MeetingConnection

    class _Speak:
        speaking = False

        async def say(self, text: str) -> None: ...
        async def cut(self) -> None: ...

    class _Room:
        async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...
        async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...
        async def mute(self, bot_id: str) -> None: ...
        async def unmute(self, bot_id: str) -> None: ...

    async def _run() -> None:
        seen: dict[str, Any] = {}

        async def _screen(value: str) -> str:
            seen["value"] = value
            return f"showing content ({len(value)} chars); no page connected yet"

        conn = MeetingConnection(
            speak=_Speak(), room=_Room(), bot_id="b1", screen=_screen
        )
        res = await conn.to_meeting("# a plan", medium="screen")
        assert res.ok is True
        assert res.medium == "screen"
        assert "showing" in res.detail.lower()
        assert seen["value"] == "# a plan"

    asyncio.run(_run())
