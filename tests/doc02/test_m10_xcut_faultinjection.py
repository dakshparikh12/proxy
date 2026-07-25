"""Doc 02 · Milestone 10 — XCUT never-throw + §12.8 single-homing, REAL behavioral oracle.

The sealed ``test_m10_xcut.py`` "proves" the never-throw boundary (AC-XCUT-07/11) with a
``"never" in src or "return" in src`` source grep guarded by skip-on-ImportError — a
tautology that passes even if a delivery verb throws. This file replaces that with a genuine
**fault-injection** oracle that exercises the real ``transport/delivery.py`` verbs on the real
emission path:

  the ``call_external`` seam's underlying client RAISES  →  a real channel emission
  (RecallTransport.post_chat / _RecallOutputMedia.write_frame / CartesiaTTS.synthesize)
  is routed through a delivery verb  →  the verb MUST return a typed ``DeliveryResult``
  with ``ok=False`` and MUST NOT propagate the throw.

This binds the node's DoD clause "each returns a typed error instead of throwing … NOT done
if a verb THROWS" to observed runtime behavior, and proves a §12.8 latency SLO number is
single-homed in ``[latency_slo]`` (never redeclared in ``[transport]``) — AC-XCUT-09.

criterion_ids: AC-XCUT-07, AC-XCUT-11 (never-throw, fault-injected) · AC-XCUT-09 (SLO single-homing)

There is deliberately NO ``pytest.skip`` / ImportError escape hatch: a genuine oracle fails
closed. If the real product code is missing or broken, this test FAILS — it does not pass by
skipping. The fault is injected at the ``libs.http.call_external`` seam (AC-XCUT-03), the sole
raw-client boundary; transport holds no raw provider client, so there is nothing else to fault.
"""
from __future__ import annotations

import asyncio
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "config" / "defaults.toml"


class _InjectedClientError(RuntimeError):
    """The failure raised INSIDE the underlying provider client (fault injected at the seam)."""


def _seam_client_raises(service_label: str) -> Callable[..., Awaitable[Any]]:
    """A ``call_external`` seam whose underlying provider client raises (the ``op`` never returns).

    Matches the real ``libs.http.call_external`` structural contract
    (:class:`transport.external.CallExternal`): ``async (op, *, service, unit_cost_usd=0.0)``.
    Here the provider round-trip explodes, exactly as a real Recall/Cartesia 5xx or a dropped
    socket would surface once retries are exhausted — the seam then propagates to its caller,
    and the delivery verb's never-throw boundary must absorb it.
    """

    async def _seam(op: Callable[[], Awaitable[Any]], *, service: str, unit_cost_usd: float = 0.0) -> Any:
        raise _InjectedClientError(f"{service_label}:{service} round-trip failed (injected)")

    return _seam


def _seam_op_raises() -> Callable[..., Awaitable[Any]]:
    """A ``call_external`` seam that faithfully INVOKES ``op`` — and ``op`` itself raises.

    This is the stricter fault: the failure originates inside the provider's raw round-trip
    closure (``_synth`` / ``_send`` / ``_api``), the seam awaits it, and the exception unwinds
    up through the emission into the delivery verb. Proves the boundary catches a fault raised
    by the real product closure, not only one raised by the seam wrapper.
    """

    async def _seam(op: Callable[[], Awaitable[Any]], *, service: str, unit_cost_usd: float = 0.0) -> Any:
        async def _boom() -> Any:
            raise _InjectedClientError(f"{service} provider closure failed (injected)")

        # Await the injected failing round-trip through the seam (as the real funnel would).
        return await _boom()

    return _seam


# ── AC-XCUT-07 / AC-XCUT-11 · never-throw under injected seam failure ──────────────────────


@pytest.mark.parametrize("seam_factory", [_seam_client_raises, _seam_op_raises])
def test_send_chat_returns_typed_error_never_throws_on_seam_failure(seam_factory: Callable[..., Any]) -> None:
    """send_chat routing a real RecallTransport chat emission returns a typed error, never throws.

    criterion_id: AC-XCUT-11
    """
    from transport.delivery import DeliveryResult, send_chat
    from transport.recall import RecallTransport

    seam = seam_factory("recall") if seam_factory is _seam_client_raises else seam_factory()
    carrier = RecallTransport(seam, api_key="from-secret-manager")

    # The real emission: post_chat -> call_external(op=self._api(...)). The seam faults.
    result = asyncio.run(send_chat(lambda: carrier.post_chat("bot-xyz", "grounded ack line")))

    assert isinstance(result, DeliveryResult), "send_chat must RETURN a DeliveryResult, not throw"
    assert result.verb == "send_chat"
    assert result.ok is False, "an emission whose seam faulted must be reported as ok=False"
    assert result.detail, "the typed error must carry the failure detail, not swallow it"


@pytest.mark.parametrize("seam_factory", [_seam_client_raises, _seam_op_raises])
def test_show_screen_returns_typed_error_never_throws_on_seam_failure(seam_factory: Callable[..., Any]) -> None:
    """show_screen routing a real Output-Media frame emission returns a typed error, never throws.

    criterion_id: AC-XCUT-11
    """
    from transport.delivery import DeliveryResult, show_screen
    from transport.media import CanvasFrame
    from transport.recall import _RecallOutputMedia

    seam = seam_factory("recall") if seam_factory is _seam_client_raises else seam_factory()
    sink = _RecallOutputMedia(seam, "bot-xyz")
    frame = CanvasFrame(data=b"", width=1, height=1, seq=0, surface="screen")

    # The real emission: write_frame -> call_external(op=self._send("output_video", ...)). Seam faults.
    result = asyncio.run(show_screen(lambda: sink.write_frame(frame)))

    assert isinstance(result, DeliveryResult), "show_screen must RETURN a DeliveryResult, not throw"
    assert result.verb == "show_screen"
    assert result.ok is False
    assert result.detail


@pytest.mark.parametrize("seam_factory", [_seam_client_raises, _seam_op_raises])
def test_speak_returns_typed_error_never_throws_on_seam_failure(seam_factory: Callable[..., Any]) -> None:
    """speak draining a real Cartesia synthesis stream returns a typed error, never throws.

    criterion_id: AC-XCUT-11
    """
    from transport.delivery import DeliveryResult, speak
    from transport.tts import CartesiaTTS

    seam = seam_factory("cartesia") if seam_factory is _seam_client_raises else seam_factory()
    tts = CartesiaTTS(seam)

    async def _synthesize_and_drain() -> None:
        # The real emission: synthesize -> _stream -> call_external(op=self._synth(text)). Seam faults.
        async for _chunk in tts.synthesize("headline text"):
            pass

    result = asyncio.run(speak(_synthesize_and_drain))

    assert isinstance(result, DeliveryResult), "speak must RETURN a DeliveryResult, not throw"
    assert result.verb == "speak"
    assert result.ok is False
    assert result.detail


def test_all_three_verbs_absorb_the_same_injected_fault() -> None:
    """The three delivery verbs are the SOLE emitters and EACH absorbs the identical injected fault.

    A single injected seam fault, fanned across the three verbs, yields three typed errors and
    zero propagated throws — the never-throw boundary is uniform across the whole delivery
    authority, not just one verb (AC-XCUT-04 sole-emitter × AC-XCUT-11 never-throw).

    criterion_id: AC-XCUT-11
    """
    from transport.delivery import DELIVERY_VERBS, DeliveryResult, send_chat, show_screen, speak
    from transport.media import CanvasFrame
    from transport.recall import RecallTransport, _RecallOutputMedia
    from transport.tts import CartesiaTTS

    async def _run() -> list[DeliveryResult]:
        chat_seam = _seam_op_raises()
        frame_seam = _seam_op_raises()
        tts_seam = _seam_op_raises()

        carrier = RecallTransport(chat_seam, api_key="from-secret-manager")
        sink = _RecallOutputMedia(frame_seam, "bot-xyz")
        tts = CartesiaTTS(tts_seam)
        frame = CanvasFrame(data=b"", width=1, height=1, seq=0, surface="screen")

        async def _drain() -> None:
            async for _chunk in tts.synthesize("headline"):
                pass

        return [
            await speak(_drain),
            await send_chat(lambda: carrier.post_chat("bot-xyz", "line")),
            await show_screen(lambda: sink.write_frame(frame)),
        ]

    results = asyncio.run(_run())

    assert {r.verb for r in results} == DELIVERY_VERBS, "the three sole delivery verbs must all report"
    assert all(isinstance(r, DeliveryResult) for r in results)
    assert all(r.ok is False for r in results), "every faulted emission must return ok=False, none may throw"


def test_verb_returns_error_even_when_underlying_client_is_a_bare_client_exception() -> None:
    """The boundary absorbs a plausible provider SDK exception subclass, not only our sentinel.

    Guards against a boundary that narrowly catches only a known error type: a real Cartesia/
    Recall client raises its own exception classes (timeouts, HTTP errors), which subclass
    ``Exception``. Inject one and confirm the verb still returns a typed error.

    criterion_id: AC-XCUT-11
    """
    from transport.delivery import DeliveryResult, speak

    class _ProviderTimeout(Exception):
        """Stand-in for a real provider SDK timeout/HTTP error (subclasses Exception)."""

    async def _emit_that_raises_provider_error() -> None:
        raise _ProviderTimeout("cartesia 504 gateway timeout")

    result = asyncio.run(speak(_emit_that_raises_provider_error))

    assert isinstance(result, DeliveryResult)
    assert result.ok is False
    assert "cartesia 504" in result.detail, "the verb must surface the real provider error, honestly"


# ── AC-XCUT-09 · §12.8 latency SLO single-homing ───────────────────────────────────────────


def _config_table() -> dict[str, Any]:
    with _CONFIG_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_sec_12_8_slo_numbers_are_single_homed_not_redeclared_in_transport() -> None:
    """The §12.8 latency SLOs live ONLY in [latency_slo]; [transport] never redeclares one.

    A duplicated latency number in [transport] would create a second source of truth for the
    pinned §12.8 contract. This asserts the real config: the SLO keys and the transport keys
    are disjoint, and no latency-shaped key (a p50/p95/ack_audible SLO) leaks into [transport].

    criterion_id: AC-XCUT-09
    """
    table = _config_table()
    slo = table["latency_slo"]
    transport = table["transport"]

    # The §12.8 SLO keys are exactly the pinned latency contract — assert they are present and homed.
    expected_slo_keys = {
        "first_text_p50_s",
        "first_text_p95_s",
        "first_audio_p50_s",
        "first_audio_p95_s",
        "ack_audible_p95_ms",
    }
    assert expected_slo_keys <= set(slo), "the §12.8 latency SLOs must be single-homed in [latency_slo]"

    # No key overlap: [transport] must not redeclare ANY [latency_slo] key.
    overlap = set(slo) & set(transport)
    assert not overlap, f"§12.8 SLO key(s) redeclared in [transport] — second source of truth: {overlap}"

    # No latency-shaped SLO key may appear in [transport] under a different spelling.
    latency_shaped = [
        k for k in transport if k.endswith(("_p50_s", "_p95_s")) or "p50" in k or "p95" in k or "ack_audible" in k
    ]
    assert not latency_shaped, f"latency-SLO-shaped tunable(s) leaked into [transport]: {latency_shaped}"


def test_transport_config_module_reads_slo_from_latency_slo_not_transport() -> None:
    """The transport config surface exposes only [transport] tunables — never a §12.8 SLO number.

    Reads the real ``transport.config`` module: every tunable it can serve comes from the
    [transport] block; none of the §12.8 SLO keys are readable through it, so transport code
    cannot accidentally bind a duplicated latency number.

    criterion_id: AC-XCUT-09
    """
    from transport import config as transport_config

    table = _config_table()
    for slo_key in table["latency_slo"]:
        # A §12.8 SLO key must not be a transport tunable — get_int/get_float would KeyError on it,
        # because config._DEFAULTS carries only [transport] numbers, not the [latency_slo] SLOs.
        assert slo_key not in transport_config._DEFAULTS, (
            f"§12.8 SLO {slo_key!r} is readable as a [transport] tunable — single-homing broken"
        )
