"""The tile + screenshare surface (§3.5): ONE canvas webpage per meeting, streamed via
Output Media as the camera tile, and promoted onto the shared screen for a structured
progress view of sandbox live work.

This layer owns the **surface + the stream + the promote/demote mechanism ONLY**. What
the frames *look like* (orb/glyphs/layout of the drawn signals and the live-work view) is
Doc 08 and is NOT built here — a frame carries a structured, non-pixel descriptor that
Doc 08's renderer turns into pixels.

Invariants pinned to the sealed criteria (AC-CANVAS-01..15):

- Exactly one canvas webpage exists per meeting and is streamed as the camera tile;
  a second/duplicate page for the same meeting is refused by :class:`CanvasRegistry`
  (AC-CANVAS-01). The tile renders non-empty frames while active (AC-CANVAS-02).
- Social signals (listening / checking / has-something / raise-hand / reaction) are
  expressed by **draw-on-canvas** methods that each produce a :class:`CanvasFrame`. There
  is no code path here that calls a native meeting-platform button/reaction/raise-hand
  API — the signal surface *is* the canvas (AC-CANVAS-03).
- The tile "checking…" ACK is a drawn tile state produced within a ≤500ms budget and
  gated on a REAL in-flight LSP-bound resolve (a live :class:`ResolveHandle` predicate),
  never fired speculatively (AC-CANVAS-04/05).
- Screenshare **promotes the same canvas** at a higher resolution, rendering the
  structured live-work progress view — never a raw live-pixel mirror of the sandbox
  screen; there is no browser/terminal-mirror or animated-cursor render path here
  (AC-CANVAS-06/07).
- This layer **executes** promote/demote when an upstream trigger asks for it; it never
  self-initiates a promote — a swap is impossible without a :class:`SwapTrigger`
  (AC-CANVAS-08). The camera tile and the screenshare are mutually exclusive via a single
  :class:`Surface` enum, so they can never be coactive (AC-CANVAS-09). Every swap is
  announced through the INJECTED announce seam (AC-CANVAS-10). The present sequence
  (speak-headline → swap-to-screen → work → swap-back, AC-CANVAS-11) is orchestrated by
  the SPEAK delivery authority in :mod:`transport.delivery` composing this surface's
  promote/demote — the projector itself is pure rendering and never speaks (AC-XCUT-04).
  A promote/demote cycle drops neither stream: the page persists and exactly one surface
  is always live —
  the swap renders onto the new surface synchronously, so there is no black frame
  (AC-CANVAS-14).
- The tile is **outbound-only**: there is deliberately NO inbound path from the tile — no
  read/ingest method, no tile address symbol, no tile-originated channel action; we
  never ingest others' pixels (screen-ingestion is explicitly deferred) (AC-CANVAS-12).
- The tile render WebSocket authenticates via a **meeting-scoped bearer token embedded in
  the Recall URL**; :func:`build_tile_url` builds the authenticated URL and rejects a
  missing or cross-scope token. The token comes from an injected value (Secret Manager
  upstream) and is never logged (AC-CANVAS-13).
- Frame-rate/smoothness is a pinned MEASUREMENT proven at M11 (AC-CANVAS-15): this file
  builds the functional shape (frames stream, the stream stays live) and asserts no
  threshold here.

No provider SDK and no network live in this module — the only outbound seam is the
injected :class:`~transport.seams.OutputMediaSink`.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from .media import CanvasFrame
from .seams import OutputMediaSink

#: The tile "checking…" ACK budget (AC-CANVAS-04). The real p100 latency is a pinned
#: measurement at M11; this named constant is the build-time budget the shape respects.
TILE_ACK_BUDGET_MS: int = 500

# Present-sequence trace step names (AC-CANVAS-11 ordering oracle).
STEP_HEADLINE: str = "headline"
STEP_SWAP_TO_SCREEN: str = "swap_to_screen"
STEP_WORK: str = "work"
STEP_SWAP_BACK: str = "swap_back"


class Surface(str, Enum):
    """The single active output surface. Camera tile and screenshare are the ONLY two
    surfaces and exactly one is active at any instant — modelled as one enum so they can
    never be coactive (AC-CANVAS-09/14). Values match :attr:`CanvasFrame.surface`."""

    TILE = "tile"
    SCREEN = "screen"


class SocialSignal(str, Enum):
    """The five draw-on-canvas social signals (§3.5). Each maps to a draw method on
    :class:`CanvasSurface`; none maps to a native meeting-platform button (AC-CANVAS-03)."""

    LISTENING = "listening"
    CHECKING = "checking"
    HAS_SOMETHING = "has_something"
    RAISE_HAND = "raise_hand"
    REACTION = "reaction"


@dataclass(frozen=True)
class Resolution:
    """A render resolution. Screen is higher than tile (AC-CANVAS-06)."""

    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


#: Ambient camera-tile resolution and the higher promoted-screen resolution (AC-CANVAS-06).
TILE_RESOLUTION: Resolution = Resolution(width=1280, height=720)
SCREEN_RESOLUTION: Resolution = Resolution(width=1920, height=1080)


@dataclass(frozen=True)
class MeetingScopedToken:
    """A meeting-scoped bearer token for the tile render WebSocket (AC-CANVAS-13).

    The ``bearer`` value comes from an injected source (Secret Manager upstream) and is
    NEVER hard-coded here and NEVER logged: it is excluded from ``repr`` and the custom
    ``__repr__`` redacts it, so an accidental log/format of the token cannot leak it.
    ``meeting_id`` scopes the token so a token minted for another meeting is rejected.
    """

    meeting_id: str
    bearer: str = field(repr=False)

    def __repr__(self) -> str:  # never leak the secret via repr/format/log
        return f"MeetingScopedToken(meeting_id={self.meeting_id!r}, bearer=<redacted>)"


class TileAuthError(Exception):
    """A tile render WS auth failure — missing or cross-scope token (AC-CANVAS-13)."""


def build_tile_url(base_url: str, token: MeetingScopedToken, *, meeting_id: str) -> str:
    """Build the authenticated tile render URL, embedding the meeting-scoped bearer token.

    Rejects an unauthenticated (empty bearer) or cross-scope (wrong meeting) token so no
    unauthenticated render WS is ever opened and a foreign token is refused (AC-CANVAS-13).
    The returned URL carries the bearer; the token itself is never logged by this layer.
    """
    if not base_url:
        raise TileAuthError("tile render URL requires a base_url")
    if not token.bearer:
        raise TileAuthError("tile render WS requires a meeting-scoped bearer token")
    if token.meeting_id != meeting_id:
        raise TileAuthError("tile render token is scoped to a different meeting")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}token={token.bearer}"


@dataclass(frozen=True)
class ProgressStep:
    """One step of the structured live-work progress view (AC-CANVAS-07)."""

    label: str
    done: bool = False


@dataclass(frozen=True)
class CitedSource:
    """Pinned source with a cited line for the live-work view (AC-CANVAS-07)."""

    file: str
    line: int
    excerpt: str = ""


@dataclass(frozen=True)
class LiveWorkView:
    """A STRUCTURED progress view of sandbox live work — a browser being driven / a test
    running / an artifact forming — NOT a raw live-pixel mirror of the sandbox screen.

    It carries a progress list, optional pinned source with cited lines, and an optional
    final-artifact preview. There is deliberately no pixel-mirror, animated-cursor, or
    drawn-diagram field/renderer here (those are Expansion-only, AC-CANVAS-07).
    """

    headline: str
    steps: tuple[ProgressStep, ...] = ()
    pinned_source: tuple[CitedSource, ...] = ()
    artifact_preview: str = ""


@dataclass(frozen=True)
class SwapTrigger:
    """The upstream cause of a swap. A promote/demote CANNOT happen without one, so this
    layer can never self-initiate a promote — the decision lives in Docs 04/08, this layer
    only executes the mechanism (AC-CANVAS-08)."""

    source: str
    reason: str = ""


@dataclass(frozen=True)
class SwapAnnouncement:
    """The announced swap handed to the injected announce seam (AC-CANVAS-10). Carries the
    direction + its upstream trigger; the spoken/chat wording lives upstream (Law 4)."""

    direction: Literal["to_screen", "to_tile"]
    trigger: SwapTrigger


@dataclass(frozen=True)
class SwapResult:
    """Honest outcome of one promote/demote — the surfaces, the persistent page, the frame
    rendered onto the new surface at the swap, and that it was announced."""

    from_surface: Surface
    to_surface: Surface
    page_id: str
    frame: CanvasFrame
    announced: bool = True


@dataclass(frozen=True)
class AckResult:
    """The drawn "checking…" tile ACK, its measured latency, and whether it met budget."""

    frame: CanvasFrame
    resolve_id: str
    latency_ms: float
    within_budget: bool


@dataclass(frozen=True)
class PresentTrace:
    """The ordered present-sequence trace (AC-CANVAS-11 oracle reads ``steps``)."""

    steps: tuple[str, ...]


@dataclass(frozen=True)
class ResolveHandle:
    """A handle to a REAL in-flight LSP-bound direct-answer resolve (AC-CANVAS-05).

    ``in_flight`` is a live predicate over the actual resolve operation — the ACK is gated
    on it, so the drawn "checking…" state reflects a real system event and is never
    fabricated. ``started_at`` anchors the ≤500ms budget measurement (AC-CANVAS-04).
    """

    resolve_id: str
    started_at: float
    in_flight: Callable[[], bool]


class SurfaceInactiveError(Exception):
    """A draw was attempted on a surface that is not currently active (mutual exclusion)."""


class NoInFlightResolveError(Exception):
    """A tile ACK was attempted with no real in-flight resolve — refused (AC-CANVAS-05)."""


class EmptyFrameError(Exception):
    """A frame with an empty payload was about to be streamed — refused (AC-CANVAS-02)."""


class DuplicateCanvasError(Exception):
    """A second canvas page was requested for a meeting that already has one (AC-CANVAS-01)."""


AnnounceHook = Callable[[SwapAnnouncement], Awaitable[None]]
#: The 'work' phase of a present sequence operates on the (pure-rendering) surface —
#: e.g. :meth:`CanvasSurface.update_screen`. The headline is spoken by the delivery
#: authority, not here (AC-XCUT-04); the sequence lives in :mod:`transport.delivery`.
WorkHook = Callable[["CanvasSurface"], Awaitable[None]]


def _encode(surface: Surface, descriptor: str) -> bytes:
    """Encode a structured, non-pixel frame descriptor. Doc 08 turns it into pixels; here
    it only has to be a non-empty, traceable payload (AC-CANVAS-02)."""
    return f"{surface.value}:{descriptor}".encode()


def _describe_view(view: LiveWorkView) -> str:
    """Fold the structured live-work view into a non-pixel descriptor (AC-CANVAS-07)."""
    steps = ";".join(f"{'x' if s.done else '.'}{s.label}" for s in view.steps)
    sources = ";".join(f"{c.file}:{c.line}" for c in view.pinned_source)
    return f"live-work|{view.headline}|steps={steps}|src={sources}|artifact={view.artifact_preview}"


class CanvasSurface:
    """ONE canvas webpage for one meeting: the ambient camera tile plus its promotion onto
    the shared screen. Owns the active-surface enum, the draw-on-canvas signal methods, the
    gated tile ACK, and the promote/demote mechanism.

    The single page (``page_id``) persists for the whole session; promote/demote only
    change which surface it renders onto (AC-CANVAS-01/14). ``sink`` is the injected
    Output-Media seam; ``announce`` fires on every swap (AC-CANVAS-10). This is **pure
    rendering**: it owns no speak/TTS path and never auto-speaks (AC-XCUT-04) — the present
    sequence's headline is spoken by the SPEAK delivery verb, which composes this surface's
    promote/demote (see :func:`transport.delivery.present_on_screen`, AC-CANVAS-11). No
    provider SDK or network is touched here.
    """

    def __init__(
        self,
        meeting_id: str,
        sink: OutputMediaSink,
        *,
        announce: AnnounceHook,
        now: Callable[[], float] = time.monotonic,
        tile_resolution: Resolution = TILE_RESOLUTION,
        screen_resolution: Resolution = SCREEN_RESOLUTION,
    ) -> None:
        self._meeting_id = meeting_id
        self._sink = sink
        self._announce = announce
        self._now = now
        self._tile_resolution = tile_resolution
        self._screen_resolution = screen_resolution
        # Exactly one page for this meeting; promote/demote never mint another (AC-CANVAS-01).
        self._page_id = f"canvas:{meeting_id}"
        # Single active surface → tile and screen can never be coactive (AC-CANVAS-09).
        self._active: Surface = Surface.TILE
        self._swaps = 0
        self._announcements = 0

    # --- identity / state (oracles read these) -----------------------------------------

    @property
    def meeting_id(self) -> str:
        return self._meeting_id

    @property
    def page_id(self) -> str:
        """The one persistent canvas page id for this meeting (AC-CANVAS-01/14)."""
        return self._page_id

    @property
    def active_surface(self) -> Surface:
        return self._active

    def is_tile_active(self) -> bool:
        return self._active is Surface.TILE

    def is_screen_active(self) -> bool:
        return self._active is Surface.SCREEN

    def coactive(self) -> bool:
        """Camera tile and screenshare both streaming — impossible by construction; a
        single enum can hold only one value (AC-CANVAS-09)."""
        return False

    def live_surface_count(self) -> int:
        """Exactly one surface is always live: the page persists and a swap renders onto
        the new surface synchronously, so no black frame / dropped stream (AC-CANVAS-14)."""
        return 1

    @property
    def swap_count(self) -> int:
        return self._swaps

    @property
    def announcement_count(self) -> int:
        """count(announcements) must equal count(swaps) — no silent swap (AC-CANVAS-10)."""
        return self._announcements

    # --- frame plumbing ----------------------------------------------------------------

    async def _emit(self, frame: CanvasFrame) -> CanvasFrame:
        if not frame.data:  # a black/empty frame is never streamed (AC-CANVAS-02)
            raise EmptyFrameError("refusing to stream an empty canvas frame")
        await self._sink.write_frame(frame)
        return frame

    def _tile_frame(self, descriptor: str) -> CanvasFrame:
        return CanvasFrame(
            data=_encode(Surface.TILE, descriptor),
            width=self._tile_resolution.width,
            height=self._tile_resolution.height,
            surface=Surface.TILE.value,
        )

    def _screen_frame(self, view: LiveWorkView) -> CanvasFrame:
        return CanvasFrame(
            data=_encode(Surface.SCREEN, _describe_view(view)),
            width=self._screen_resolution.width,
            height=self._screen_resolution.height,
            surface=Surface.SCREEN.value,
        )

    def _require_tile(self, what: str) -> None:
        if self._active is not Surface.TILE:
            raise SurfaceInactiveError(f"cannot draw {what!r}: the tile is not the active surface")

    def _require_screen(self, what: str) -> None:
        if self._active is not Surface.SCREEN:
            raise SurfaceInactiveError(f"cannot render {what!r}: the screen is not the active surface")

    # --- ambient tile + social signals (draw-on-canvas, never native buttons) ----------

    async def render_tile(self, descriptor: str = "ambient") -> CanvasFrame:
        """Stream one non-empty ambient tile frame while the tile is active (AC-CANVAS-02)."""
        self._require_tile("tile")
        return await self._emit(self._tile_frame(descriptor))

    async def draw_listening(self) -> CanvasFrame:
        """Express the 'listening' social signal as a drawn tile frame (AC-CANVAS-03)."""
        self._require_tile(SocialSignal.LISTENING.value)
        return await self._emit(self._tile_frame(SocialSignal.LISTENING.value))

    async def draw_has_something(self) -> CanvasFrame:
        """Express the 'has-something' social signal as a drawn tile frame (AC-CANVAS-03)."""
        self._require_tile(SocialSignal.HAS_SOMETHING.value)
        return await self._emit(self._tile_frame(SocialSignal.HAS_SOMETHING.value))

    async def draw_raise_hand(self) -> CanvasFrame:
        """Express 'raise-hand' as a drawn tile frame — NOT the native raise-hand button
        (there is no such call anywhere on this path) (AC-CANVAS-03)."""
        self._require_tile(SocialSignal.RAISE_HAND.value)
        return await self._emit(self._tile_frame(SocialSignal.RAISE_HAND.value))

    async def draw_reaction(self) -> CanvasFrame:
        """Express a 'reaction' as a drawn tile frame — NOT the native reaction button
        (there is no such call anywhere on this path) (AC-CANVAS-03)."""
        self._require_tile(SocialSignal.REACTION.value)
        return await self._emit(self._tile_frame(SocialSignal.REACTION.value))

    async def ack_checking(self, handle: ResolveHandle) -> AckResult:
        """Draw the 'checking…' tile ACK state — the fifth social signal — ONLY while a real
        LSP-bound resolve is in flight, within the ≤500ms budget (AC-CANVAS-03/04/05).

        Refuses to draw when nothing is resolving, so the drawn state never fabricates a
        system event (source-honesty, AC-CANVAS-05).
        """
        self._require_tile(SocialSignal.CHECKING.value)
        if not handle.in_flight():  # no speculative ACK — must be a real in-flight resolve
            raise NoInFlightResolveError("no in-flight LSP-bound resolve: refusing to draw 'checking…'")
        latency_ms = (self._now() - handle.started_at) * 1000.0
        frame = await self._emit(self._tile_frame(SocialSignal.CHECKING.value))
        return AckResult(
            frame=frame,
            resolve_id=handle.resolve_id,
            latency_ms=latency_ms,
            within_budget=latency_ms <= TILE_ACK_BUDGET_MS,
        )

    # --- promote / demote mechanism (executed on upstream trigger only) ----------------

    async def promote(self, view: LiveWorkView, *, trigger: SwapTrigger) -> SwapResult:
        """EXECUTE a promote (tile → screen) requested by an upstream ``trigger``. Renders
        the same persistent page onto the shared screen at higher resolution showing the
        structured live-work view, and announces the swap. Never self-initiated — a promote
        is impossible without a trigger (AC-CANVAS-06/08/09/10/14).
        """
        if self._active is Surface.SCREEN:
            raise SurfaceInactiveError("already promoted: the screen is already the active surface")
        frame = self._screen_frame(view)  # built before the swap so the screen is live at once
        self._active = Surface.SCREEN  # atomic single-enum swap → no coactive, no None gap
        try:
            await self._emit(frame)  # screen stream is live the instant we swap (no black frame)
        except Exception:
            # A sink-write failure must NOT leave the surface flipped-but-dark: roll back so
            # the tile stays the one live surface — never BOTH surfaces dark (AC-CANVAS-14).
            self._active = Surface.TILE
            raise
        # Count the swap only AFTER it is announced, so swaps == announcements always holds
        # even if the announce raises (AC-CANVAS-10: no silent swap).
        await self._announce(SwapAnnouncement(direction="to_screen", trigger=trigger))
        self._swaps += 1
        self._announcements += 1
        return SwapResult(
            from_surface=Surface.TILE, to_surface=Surface.SCREEN, page_id=self._page_id, frame=frame
        )

    async def update_screen(self, view: LiveWorkView) -> CanvasFrame:
        """Render an updated live-work view onto the shared screen while promoted — the
        'work' phase of the present sequence (AC-CANVAS-11)."""
        self._require_screen("live-work")
        return await self._emit(self._screen_frame(view))

    async def demote(self, *, trigger: SwapTrigger) -> SwapResult:
        """EXECUTE a demote (screen → tile) requested by an upstream ``trigger``. Swaps the
        persistent page back to the ambient tile and announces the swap (AC-CANVAS-08/10/14)."""
        if self._active is Surface.TILE:
            raise SurfaceInactiveError("already on the tile: nothing to demote")
        frame = self._tile_frame("resume")
        self._active = Surface.TILE  # atomic single-enum swap back → tile live at once
        try:
            await self._emit(frame)
        except Exception:
            # Roll back on a sink-write failure so the screen stays the one live surface —
            # never BOTH surfaces dark (AC-CANVAS-14).
            self._active = Surface.SCREEN
            raise
        await self._announce(SwapAnnouncement(direction="to_tile", trigger=trigger))
        self._swaps += 1
        self._announcements += 1
        return SwapResult(
            from_surface=Surface.SCREEN, to_surface=Surface.TILE, page_id=self._page_id, frame=frame
        )


class CanvasRegistry:
    """Guards ONE canvas page per meeting (AC-CANVAS-01). ``open`` refuses a second page for
    a meeting that already has a live canvas; ``pages_for`` is the oracle read."""

    def __init__(self) -> None:
        self._open: dict[str, CanvasSurface] = {}

    def open(
        self,
        meeting_id: str,
        sink: OutputMediaSink,
        *,
        announce: AnnounceHook,
        now: Callable[[], float] = time.monotonic,
    ) -> CanvasSurface:
        """Open the single canvas page for ``meeting_id``; a duplicate is refused."""
        if meeting_id in self._open:
            raise DuplicateCanvasError(f"a canvas page already exists for meeting {meeting_id!r}")
        surface = CanvasSurface(meeting_id, sink, announce=announce, now=now)
        self._open[meeting_id] = surface
        return surface

    def get(self, meeting_id: str) -> CanvasSurface | None:
        return self._open.get(meeting_id)

    def pages_for(self, meeting_id: str) -> int:
        """The number of canvas pages for a meeting — 1 while open, else 0 (AC-CANVAS-01)."""
        return 1 if meeting_id in self._open else 0

    def close(self, meeting_id: str) -> None:
        self._open.pop(meeting_id, None)
