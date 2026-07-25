"""Doc 02 · Milestone 9 — REAL cost-floor / accrual / carrier / matrix oracles.

The sealed ``test_m9_seam.py`` "proves" several load-bearing criteria with weak
oracles: AC-SEAM-22 is an abort-registry *source grep* (not a rate-card-binding
check), and the cost band (AC-SEAM-13) and elapsed×rate accrual (AC-SEAM-14/15)
have **no binding oracle at all**. This file authors the real ones — every test
drives the actual ``transport.cost`` / ``transport.carrier`` / ``transport.surface``
code, never a string search or a passes-by-construction constant.

The decisive move for the cost floor: we do not merely assert the floor is a
number in ``[0.75, 0.85]`` (a constant sum passes that trivially). We assert the
floor equals a computed ``elapsed × rate`` off the SAME single rate card the
accrual multiplies, and then we **inject a rate-card divergence** and prove BOTH
the floor band-check and the accrual move together. A floor that is a decoupled
constant would stay put while the accrual drifts — that is the failure this
oracle is designed to catch.

criterion_ids exercised: AC-SEAM-06, AC-SEAM-07, AC-SEAM-13, AC-SEAM-14,
AC-SEAM-15, AC-SEAM-16, AC-SEAM-17, AC-SEAM-22.

All product imports are inside test bodies (matching the sealed suite's style so
collection never depends on import order).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

pytestmark = pytest.mark.simulation

# Floating-point tolerance for USD/hr arithmetic (config rates are 2-decimal cents).
_USD_EPS = 1e-9


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


class _RateCardOverride:
    """Divergence injector: temporarily bend the single rate card at its source.

    Both :func:`transport.cost.transport_rate` (which the floor check binds) and
    :class:`transport.cost.TransportCostAccrual` (the accrual) read the per-component
    rate through ``transport.config.get_float``. Patching that one seam is exactly a
    rate-card divergence: if the floor is truly bound to the rate card it moves; if it
    is a decoupled constant sum it does not. We patch the source, not either consumer,
    so we never accidentally prove a tautology by patching only one side.
    """

    def __init__(self, deltas: dict[str, float]) -> None:
        self._deltas = deltas
        self._config: Any = None
        self._orig: Callable[[str], float] | None = None

    def __enter__(self) -> "_RateCardOverride":
        from transport import config

        self._config = config
        self._orig = config.get_float
        orig = self._orig
        deltas = self._deltas

        def _bent(key: str) -> float:
            base = orig(key)
            return base + deltas.get(key, 0.0)

        # Replace the single rate-card read seam at its source (setattr keeps the
        # dynamic override honest under mypy --strict; it is a module-level attr swap).
        setattr(config, "get_float", _bent)
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._config is not None and self._orig is not None
        setattr(self._config, "get_float", self._orig)


# ── AC-SEAM-22 / AC-SEAM-13: the floor BINDS the accrual rate card ──────────────


def test_floor_equals_computed_elapsed_times_rate_off_the_one_rate_card() -> None:
    """AC-SEAM-22: the managed floor equals elapsed×rate off the SINGLE rate card.

    Not "the floor is some constant in the band" — the floor's own aggregate rate
    is reconstructed from the per-component ``rate_card`` and shown to be exactly
    what the accrual would charge for one meeting-hour. Same card, same number.

    criterion_id: AC-SEAM-22
    """
    from transport import cost

    card = cost.rate_card()
    # The aggregate the floor band-checks is the plain sum of the per-component card.
    assert abs(cost.transport_rate() - sum(card.values())) < _USD_EPS

    # One meeting-hour of accrual off the DEFAULT rate must equal that same aggregate
    # rate — proving the accrual multiplies the identical card the floor binds.
    accrual = cost.TransportCostAccrual.new()
    one_hour_cost = accrual.transport_usd(1.0)
    assert abs(one_hour_cost - cost.transport_rate()) < _USD_EPS
    assert abs(one_hour_cost - sum(card.values())) < _USD_EPS

    # And that number honestly sits in the managed band (AC-SEAM-13) — because the
    # real card sums there, not because a constant was hard-coded into the band.
    assert cost.MANAGED_FLOOR_USD_PER_HR <= one_hour_cost <= cost.MANAGED_CEILING_USD_PER_HR
    assert cost.floor_within_managed_ceiling() is True


def test_floor_tracks_a_rate_card_divergence_not_a_constant_sum() -> None:
    """AC-SEAM-22: inject a rate-card divergence — floor AND accrual move together.

    This is the oracle that catches a decoupled constant. Bend one component of the
    single rate card upward; a floor bound to the card sees the new aggregate (and
    now reads out-of-band), and the accrual charges the new rate for the same hour.
    A constant-sum floor would stay at $0.80 while the accrual drifted — that split
    is the exact defect AC-SEAM-22 forbids, and this test fails loudly on it.

    criterion_id: AC-SEAM-22
    """
    from transport import cost

    base_rate = cost.transport_rate()
    base_hour = cost.TransportCostAccrual.new().transport_usd(1.0)
    assert cost.floor_within_managed_ceiling() is True  # baseline: in band

    # Push TTS up by $0.30/hr → aggregate 0.80 → 1.10, out of the [0.75, 0.85] band.
    with _RateCardOverride({"tts_usd_per_hr": 0.30}):
        bent_rate = cost.transport_rate()
        bent_hour = cost.TransportCostAccrual.new().transport_usd(1.0)

        # The floor's bound aggregate MOVED with the card (not a frozen constant).
        assert abs(bent_rate - (base_rate + 0.30)) < _USD_EPS
        # The accrual charges the SAME moved rate for one hour — one card, one number.
        assert abs(bent_hour - bent_rate) < _USD_EPS
        assert abs(bent_hour - (base_hour + 0.30)) < _USD_EPS
        # And the band-check now reports out-of-band — proving it reads the live card,
        # not a constant that would have stayed comfortably "in band".
        assert cost.floor_within_managed_ceiling() is False

    # Divergence reverted cleanly: the floor is back in band and the rate restored.
    assert abs(cost.transport_rate() - base_rate) < _USD_EPS
    assert cost.floor_within_managed_ceiling() is True


def test_floor_tracks_a_downward_divergence_below_the_band() -> None:
    """AC-SEAM-22: a downward card divergence pushes the floor BELOW the band too.

    Symmetry proof: the band-check is a real two-sided ``[floor, ceiling]`` binding
    on the live card, so shrinking the card drops the aggregate under $0.75. A
    constant sum could never read below its own floor.

    criterion_id: AC-SEAM-13
    """
    from transport import cost

    # Drop the bot rate by $0.20/hr → aggregate 0.80 → 0.60, below the $0.75 floor.
    with _RateCardOverride({"bot_usd_per_hr": -0.20}):
        assert cost.transport_rate() < cost.MANAGED_FLOOR_USD_PER_HR
        assert cost.floor_within_managed_ceiling() is False
        # The accrual tracks the shrunken card identically.
        assert abs(cost.TransportCostAccrual.new().transport_usd(1.0) - cost.transport_rate()) < _USD_EPS


# ── AC-SEAM-14: elapsed × rate accrual, not a flat all-in constant ──────────────


def test_accrual_is_linear_in_elapsed_time_off_the_one_rate() -> None:
    """AC-SEAM-14: cost == elapsed × rate off the single rate card, at every point.

    Drive the accrual across a span of elapsed hours and assert each reading is
    exactly ``elapsed × transport_rate`` — a straight line through the origin, never
    a flat $1 all-in. Zero elapsed accrues zero; the slope is precisely the card.

    criterion_id: AC-SEAM-14
    """
    from transport import cost

    rate = cost.transport_rate()
    accrual = cost.TransportCostAccrual.new()

    assert accrual.transport_usd(0.0) == 0.0  # no time → no spend (not a flat constant)

    prev = 0.0
    for elapsed in (0.1, 0.25, 0.5, 1.0, 2.0):
        got = accrual.transport_usd(elapsed)
        assert abs(got - elapsed * rate) < _USD_EPS  # cost == elapsed × rate, exactly
        assert got > prev  # strictly increasing in elapsed → genuinely time-driven
        prev = got

    # Cross-check the slope directly: (cost(2h) − cost(1h)) is exactly one more hour's rate.
    assert abs((accrual.transport_usd(2.0) - accrual.transport_usd(1.0)) - rate) < _USD_EPS


def test_accrual_is_not_a_flat_all_in_dollar() -> None:
    """AC-SEAM-14: the accrual is NOT the false flat $1/hr all-in constant.

    A common wrong implementation pins transport at a flat all-in dollar. This
    asserts a short meeting costs strictly less than a long one and than $1 — the
    accrual is a computed fraction of the rate, never a headline flat charge.

    criterion_id: AC-SEAM-14
    """
    from transport import cost

    accrual = cost.TransportCostAccrual.new()
    six_min = accrual.transport_usd(0.1)   # 6 minutes
    one_hour = accrual.transport_usd(1.0)
    assert six_min < one_hour
    assert six_min < cost.ALLIN_CEILING_USD_PER_HR
    assert abs(six_min - 0.1 * one_hour) < _USD_EPS  # 6 min is exactly a tenth of the hour


# ── AC-SEAM-15: accrual RELOADS monotonically across a recycle ───────────────────


def test_accrual_reloads_monotonically_across_a_recycle() -> None:
    """AC-SEAM-15: after a harness recycle the accrual resumes, never resets to 0.

    Accrue some cost, "recycle" by constructing a fresh accrual via ``reload`` with
    the prior total, and assert the resumed value is ≥ the pre-recycle value — the
    cost is monotonic non-decreasing across the recycle, and continues to accrue at
    the same single rate. A reset-to-0 (the defect AC-SEAM-15 forbids) fails here.

    criterion_id: AC-SEAM-15
    """
    from transport import cost

    rate = cost.transport_rate()
    before = cost.TransportCostAccrual.new()
    accrued_before = before.transport_usd(0.5)  # half an hour in before the recycle
    assert accrued_before > 0.0

    # Recycle: resume with the already-accrued cost carried across (reload, not reset).
    after = cost.TransportCostAccrual.reload(accrued_before)
    resumed_at_zero_more = after.transport_usd(0.0)
    assert abs(resumed_at_zero_more - accrued_before) < _USD_EPS  # reload, NOT reset to 0

    # A further half-hour after the recycle keeps accruing at the same rate: total is
    # the carried prior plus the new elapsed×rate — monotonic and rate-consistent.
    total_after = after.transport_usd(0.5)
    assert total_after >= accrued_before  # monotonic non-decreasing across recycle
    assert abs(total_after - (accrued_before + 0.5 * rate)) < _USD_EPS


# ── AC-SEAM-06/07: the carrier is a PURE in-process asyncio fan-out (no bus) ──────


def test_carrier_is_a_pure_in_process_asyncio_fan_out() -> None:
    """AC-SEAM-06/07: two subscribers each receive EVERY emitted signal, in order.

    Behavioral no-bus proof (not a source grep for 'kafka'): the carrier holds only
    in-process ``asyncio.Queue`` objects, ``emit`` is a plain awaitable enqueue, and
    two independent subscribers each drain the FULL signal stream in order. A broker
    would fan out through an external queue; here the fan-out is the queue list itself.

    criterion_id: AC-SEAM-06
    """
    import asyncio as _asyncio

    from transport.carrier import SignalCarrier
    from transport.signals import BargeIn, BotStatus, ChatMessage, RosterEvent, Speaking, Transcript

    async def _exercise() -> tuple[list[Any], list[Any], list[Any]]:
        carrier = SignalCarrier()
        sub_a = carrier.subscribe()
        sub_b = carrier.subscribe()

        # The subscribers are backed by real asyncio.Queue objects — the only carrier
        # primitive on the emit path (no bus/broker object anywhere in the fan-out).
        # Access the private list purely to assert the fan-out substrate is in-process.
        queues = carrier._subscribers  # noqa: SLF001 — structural no-bus assertion
        assert len(queues) == 2
        assert all(isinstance(q, _asyncio.Queue) for q in queues)

        stream: list[Any] = [
            Transcript(words="p95 is 340ms", speaker="Sam", t=0.0),
            RosterEvent(kind="join", name="Maya", participant_id="p1"),
            Speaking(on=True, t=0.1),
            BargeIn(t=0.2),
            ChatMessage(message="showing the trace", sender="Proxy"),
            BotStatus(status="rejoined", t=0.3),
        ]
        for sig in stream:
            await carrier.emit(sig)
        carrier.close()

        got_a = [s async for s in sub_a]
        got_b = [s async for s in sub_b]
        return stream, got_a, got_b

    stream, got_a, got_b = _run(_exercise())
    # Every subscriber saw every signal, in emit order — genuine fan-out, no drops.
    assert got_a == stream
    assert got_b == stream
    # Fan-out really duplicated the stream (both got the full set independently).
    assert got_a is not got_b and len(got_a) == len(got_b) == len(stream)


def test_carrier_emit_touches_no_network_stack() -> None:
    """AC-SEAM-07: emit is a direct in-process await — no socket/network I/O at all.

    We run the carrier with the socket module disabled: if ``emit`` reached for a
    wire (a broker, a websocket, an HTTP client) it would try to construct a socket
    and raise. It completes cleanly, proving the path to consumers never leaves the
    process.

    criterion_id: AC-SEAM-07
    """
    import socket as _socket

    from transport.carrier import SignalCarrier
    from transport.signals import Transcript

    original_socket = _socket.socket

    def _forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("carrier.emit opened a network socket — not an in-process fan-out")

    async def _exercise() -> list[Any]:
        carrier = SignalCarrier()
        sub = carrier.subscribe()
        setattr(_socket, "socket", _forbidden)
        try:
            await carrier.emit(Transcript(words="in process", speaker="Sam", t=0.0))
        finally:
            setattr(_socket, "socket", original_socket)
        carrier.close()
        return [s async for s in sub]

    got = _run(_exercise())
    assert len(got) == 1 and got[0].words == "in process"


# ── AC-SEAM-16/17: the 15-cell platform matrix routes with zero per-platform code ─


def test_platform_matrix_is_fifteen_cells_through_one_carrier() -> None:
    """AC-SEAM-16/17: 5 capabilities × 3 platforms = 15 cells, all one carrier.

    The matrix is (join/hear/speak/tile/screenshare) × (Meet/Zoom/Teams). Every cell
    resolves through the single ``TransportProvider`` seam with zero per-platform
    branches — so the matrix is uniformly reachable and complete (15 distinct cells).

    criterion_id: AC-SEAM-16
    """
    from transport import surface

    assert set(surface.CAPABILITIES) == {"join", "hear", "speak", "tile", "screenshare"}
    assert set(surface.PLATFORMS) == {"meet", "zoom", "teams"}

    matrix = surface.platform_matrix()
    assert len(matrix) == 15  # 5 × 3, no cell missing or duplicated
    assert set(matrix.keys()) == {(p, c) for p in surface.PLATFORMS for c in surface.CAPABILITIES}
    assert all(matrix.values())  # every cell reachable through the one carrier


def test_transport_provider_carries_every_capability_with_no_per_platform_method() -> None:
    """AC-SEAM-17: the ONE TransportProvider seam covers all five capabilities and
    forks on NO meeting platform — zero per-platform methods on the seam.

    We check the Protocol surface itself: it exposes the capability verbs (join,
    output-media for speak/tile/screenshare, roster/chat/status for hear) and carries
    no ``*_meet`` / ``*_zoom`` / ``*_teams`` method — a per-platform branch would show
    up as a platform-suffixed member. One seam spans all three platforms.

    criterion_id: AC-SEAM-17
    """
    from transport import seams, surface

    provider_members = {name for name in dir(seams.TransportProvider) if not name.startswith("_")}
    # The seam exposes the capability verbs (join + the media/event surface) …
    assert "join" in provider_members
    assert "output_media" in provider_members  # speak / tile / screenshare stream through here
    assert {"roster_events", "chat_events"} <= provider_members  # hear (roster + chat + transcript)

    # … and NOT one method is platform-specialised (no join_meet / speak_zoom / …).
    for member in provider_members:
        for platform in surface.PLATFORMS:
            assert not member.endswith(platform), f"per-platform seam method leaked: {member}"
            assert platform not in member, f"platform name in seam method: {member}"
