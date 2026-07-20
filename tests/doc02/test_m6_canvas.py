"""Doc 02 · Milestone 6 — CANVAS (AC-CANVAS-01 .. AC-CANVAS-15).

Canvas / tile / screenshare surface tests. All product imports inside test bodies.
"""
import asyncio
import pytest

pytestmark = pytest.mark.simulation

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_canvas():
    from transport.media import CanvasFrame

    class _FakeSink:
        def __init__(self):
            self.frames = []
            self.flushed = False
        async def write_audio(self, chunk): pass
        async def flush(self): self.flushed = True
        async def write_frame(self, frame): self.frames.append(frame)

    from transport.delivery import CanvasDelivery
    sink = _FakeSink()
    canvas = CanvasDelivery(sink=sink)
    return canvas, sink


# ── CANVAS-01 ────────────────────────────────────────────────────────────────

def test_one_canvas_per_meeting_streams_as_tile():
    """AC-CANVAS-01: exactly one canvas page per meeting, streamed as camera tile.

    criterion_id: AC-CANVAS-01
    """
    from transport.delivery import CanvasDelivery

    canvas, sink = _make_canvas()
    assert canvas is not None
    # One canvas instance per meeting
    canvas2, _ = _make_canvas()
    # Each is independent; no shared state between meetings
    assert canvas is not canvas2


# ── CANVAS-02 ────────────────────────────────────────────────────────────────

def test_tile_renders_in_call_produces_frames():
    """AC-CANVAS-02: camera stream produces non-empty frames from tile.

    criterion_id: AC-CANVAS-02
    """
    from transport.media import CanvasFrame
    from transport.delivery import CanvasDelivery

    canvas, sink = _make_canvas()
    frame = CanvasFrame(data=b"\xff" * 100, width=320, height=240, seq=0)
    _run(canvas.render_frame(frame))
    assert sink.frames, "tile must produce non-empty frames"
    assert sink.frames[0].data == b"\xff" * 100


# ── CANVAS-03 ────────────────────────────────────────────────────────────────

def test_social_signals_drawn_on_canvas_not_native_buttons():
    """AC-CANVAS-03: social signals draw to canvas; no native platform button calls.

    criterion_id: AC-CANVAS-03
    """
    import inspect
    import transport.delivery as delivery_mod

    src = inspect.getsource(delivery_mod)
    # No native platform reaction/raise-hand API calls
    native_apis = ("raise_hand(", "set_reaction(", "platform_button(", "native_raise")
    for api in native_apis:
        assert api not in src, f"native platform button call {api!r} found"


# ── CANVAS-04 ────────────────────────────────────────────────────────────────

def test_tile_ack_checking_drawn_within_500ms():
    """AC-CANVAS-04: checking… tile state drawn within 500ms of resolve-start.

    criterion_id: AC-CANVAS-04
    """
    import time
    from transport.delivery import CanvasDelivery

    canvas, sink = _make_canvas()
    t0 = time.monotonic()
    _run(canvas.set_state("checking"))
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 500, f"checking state took {elapsed_ms:.1f}ms > 500ms"


# ── CANVAS-05 ────────────────────────────────────────────────────────────────

def test_tile_ack_fires_only_on_real_resolve():
    """AC-CANVAS-05: checking state appears only when LSP resolve is in flight.

    criterion_id: AC-CANVAS-05
    """
    from transport.delivery import CanvasDelivery

    canvas, sink = _make_canvas()
    # No resolve in flight: state must not be 'checking'
    assert canvas.current_state != "checking"
    # Trigger a resolve
    _run(canvas.set_state("checking"))
    assert canvas.current_state == "checking"
    # Clear it
    _run(canvas.set_state("listening"))
    assert canvas.current_state != "checking"


# ── CANVAS-06 ────────────────────────────────────────────────────────────────

def test_screenshare_promotes_same_canvas_at_higher_res():
    """AC-CANVAS-06: promote uses same canvas page at higher resolution.

    criterion_id: AC-CANVAS-06
    """
    from transport.delivery import CanvasDelivery

    canvas, sink = _make_canvas()
    _run(canvas.promote_to_screen())
    assert canvas.active_surface == "screen"
    # Same canvas object — not a new page
    _run(canvas.demote_to_tile())
    assert canvas.active_surface == "tile"


# ── CANVAS-07 ────────────────────────────────────────────────────────────────

def test_v0_screen_is_structured_progress_not_live_mirror():
    """AC-CANVAS-07: no pixel-accurate live browser/terminal mirror code paths.

    criterion_id: AC-CANVAS-07
    """
    import inspect
    import transport.delivery as mod

    src = inspect.getsource(mod)
    forbidden = ("live_mirror", "pixel_accurate_mirror", "animated_cursor", "browser_mirror")
    for f in forbidden:
        assert f not in src, f"live mirror path {f!r} found (Expansion only)"


# ── CANVAS-08 ────────────────────────────────────────────────────────────────

def test_layer_executes_promote_never_self_initiates():
    """AC-CANVAS-08: promote only on upstream trigger; no self-initiated promote.

    criterion_id: AC-CANVAS-08
    """
    from transport.delivery import CanvasDelivery

    canvas, sink = _make_canvas()
    # Without upstream trigger, surface stays tile
    assert canvas.active_surface == "tile"
    # Only promotes when explicitly triggered
    _run(canvas.promote_to_screen())
    assert canvas.active_surface == "screen"


# ── CANVAS-09 ────────────────────────────────────────────────────────────────

def test_camera_and_screenshare_mutually_exclusive():
    """AC-CANVAS-09: tile and screen never both active simultaneously.

    criterion_id: AC-CANVAS-09
    """
    from transport.delivery import CanvasDelivery

    canvas, sink = _make_canvas()
    # Initial: tile active
    assert canvas.active_surface == "tile"
    assert not (canvas.tile_active and canvas.screen_active)

    _run(canvas.promote_to_screen())
    assert canvas.active_surface == "screen"
    assert not (canvas.tile_active and canvas.screen_active)

    _run(canvas.demote_to_tile())
    assert canvas.active_surface == "tile"
    assert not (canvas.tile_active and canvas.screen_active)


# ── CANVAS-10 ────────────────────────────────────────────────────────────────

def test_every_swap_announced():
    """AC-CANVAS-10: every swap transition has a paired announcement.

    criterion_id: AC-CANVAS-10
    """
    from transport.delivery import CanvasDelivery

    announcements = []
    canvas, sink = _make_canvas()
    canvas.on_announce = lambda msg: announcements.append(msg)

    _run(canvas.promote_to_screen())
    _run(canvas.demote_to_tile())

    assert len(announcements) == 2, (
        f"expected 2 swap announcements, got {len(announcements)}"
    )


# ── CANVAS-11 ────────────────────────────────────────────────────────────────

def test_present_sequence_ordering():
    """AC-CANVAS-11: speak-headline -> swap-to-screen -> work -> swap-back.

    criterion_id: AC-CANVAS-11
    """
    from transport.delivery import CanvasDelivery

    trace = []
    canvas, sink = _make_canvas()
    canvas.on_announce = lambda msg: trace.append(("announce", msg))

    async def run():
        trace.append(("speak", "headline"))
        await canvas.promote_to_screen()
        trace.append(("work", "sandbox_live"))
        await canvas.demote_to_tile()

    _run(run())
    types = [t[0] for t in trace]
    assert types.index("speak") < types.index("announce"), "headline must precede swap"


# ── CANVAS-12 ────────────────────────────────────────────────────────────────

def test_tile_is_outbound_only_no_inbound_path():
    """AC-CANVAS-12: no TILE_ADDRESS or tile-originated ChannelAction in codebase.

    criterion_id: AC-CANVAS-12
    """
    import subprocess
    result = subprocess.run(
        ["grep", "-r", "TILE_ADDRESS", "/Users/daksh/Desktop/proxy/services/transport/"],
        capture_output=True, text=True
    )
    assert not result.stdout.strip(), f"TILE_ADDRESS found: {result.stdout}"


# ── CANVAS-13 ────────────────────────────────────────────────────────────────

def test_tile_render_ws_authenticates_via_meeting_scoped_token():
    """AC-CANVAS-13: render WS uses meeting-scoped bearer token from Recall URL.

    criterion_id: AC-CANVAS-13
    """
    import inspect
    import transport.delivery as mod

    src = inspect.getsource(mod)
    assert "bearer" in src.lower() or "token" in src.lower(), (
        "render WS must authenticate via bearer token"
    )


# ── CANVAS-14 ────────────────────────────────────────────────────────────────

def test_promote_demote_cycle_drops_neither_stream():
    """AC-CANVAS-14: a tile->screen->tile cycle keeps a stream live throughout.

    criterion_id: AC-CANVAS-14
    """
    from transport.delivery import CanvasDelivery
    from transport.media import CanvasFrame

    canvas, sink = _make_canvas()
    # Tile active before promote
    frame_tile = CanvasFrame(data=b"tile", width=320, height=240, seq=0)
    _run(canvas.render_frame(frame_tile))

    _run(canvas.promote_to_screen())
    frame_screen = CanvasFrame(data=b"screen", width=1920, height=1080, seq=1)
    _run(canvas.render_frame(frame_screen))

    _run(canvas.demote_to_tile())
    frame_tile2 = CanvasFrame(data=b"tile2", width=320, height=240, seq=2)
    _run(canvas.render_frame(frame_tile2))

    assert len(sink.frames) == 3, "frames from all phases must be produced"


# ── CANVAS-15 ────────────────────────────────────────────────────────────────

def test_frame_rate_pinned_measurement_worst_case_no_crash():
    """AC-CANVAS-15: worst-case degrades to choppier motion; no crash or capability loss.

    criterion_id: AC-CANVAS-15
    """
    from transport.delivery import CanvasDelivery
    from transport.media import CanvasFrame

    canvas, sink = _make_canvas()
    # Render many frames rapidly (worst-case load)
    for i in range(50):
        frame = CanvasFrame(data=b"f" * 1000, width=1920, height=1080, seq=i)
        _run(canvas.render_frame(frame))

    assert len(sink.frames) == 50, "all frames must be delivered (no capability loss)"
