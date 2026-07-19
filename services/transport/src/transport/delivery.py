"""Delivery verbs + cross-cutting guards (M10, AC-XCUT-01/02/04/07/09/10/11).

The three delivery verbs — **speak · send_chat · show_screen** — are the SOLE delivery
authority (AC-XCUT-04): every channel emission flows through one of them, and each returns
a typed result and **never throws** (AC-XCUT-11, the agentkit never-throw boundary). The
projector/canvas is pure rendering — it has no ``speak``/TTS path and never auto-speaks.

This module also collects the layer's guard facts for the fresh-context checks:
- every user-visible transport string (consent notice, ack line, gap line, voice-down
  notice) carries no internal component name — the naming lint over
  :func:`user_visible_strings` (AC-XCUT-01);
- the §12.8-pinned latency SLOs stay single-homed in ``[latency_slo]``; the ``[transport]``
  tunables never redeclare a §12.8 number (AC-XCUT-09);
- no screen-ingestion path exists — the canvas is outbound-only (AC-XCUT-10).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .canvas import (
    STEP_HEADLINE,
    STEP_SWAP_BACK,
    STEP_SWAP_TO_SCREEN,
    STEP_WORK,
    CanvasSurface,
    LiveWorkView,
    PresentTrace,
    SwapTrigger,
    WorkHook,
)
from .consent import consent_notice
from .failure import (
    _VOICE_DOWN_NOTICE,
    HONEST_STOP_REJOIN_FAILED,
    HONEST_STOP_SECOND_DROP,
    Gap,
)
from .speak import CANNED_ACKS

#: The sole delivery verbs — the only emitters of any channel output (AC-XCUT-04).
DELIVERY_VERBS: frozenset[str] = frozenset({"speak", "send_chat", "show_screen"})

#: Speaks one line via the SPEAK delivery authority (wired to the speak orchestrator).
SpeakHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class DeliveryResult:
    """A delivery-verb outcome — ``ok`` false carries the typed error; the verb never raised."""

    verb: str
    ok: bool
    detail: str = ""


def _failed(verb: str, exc: BaseException) -> DeliveryResult:
    """The typed error a verb returns instead of throwing (never-throw boundary)."""
    return DeliveryResult(verb=verb, ok=False, detail=f"{verb} failed: {exc}")


async def speak(run: Callable[[], Awaitable[Any]]) -> DeliveryResult:
    """Deliver voice via the speak orchestrator; return a typed error, never throw."""
    try:
        await run()
        return DeliveryResult(verb="speak", ok=True)
    except Exception as exc:  # noqa: BLE001 - contract: return errors, never throw (AC-XCUT-11)
        return _failed("speak", exc)


async def send_chat(run: Callable[[], Awaitable[Any]]) -> DeliveryResult:
    """Deliver chat (broadcast or DM); return a typed error, never throw."""
    try:
        await run()
        return DeliveryResult(verb="send_chat", ok=True)
    except Exception as exc:  # noqa: BLE001 - contract: return errors, never throw (AC-XCUT-11)
        return _failed("send_chat", exc)


async def show_screen(run: Callable[[], Awaitable[Any]]) -> DeliveryResult:
    """Promote/demote the shared screen; return a typed error, never throw."""
    try:
        await run()
        return DeliveryResult(verb="show_screen", ok=True)
    except Exception as exc:  # noqa: BLE001 - contract: return errors, never throw (AC-XCUT-11)
        return _failed("show_screen", exc)


async def present_on_screen(
    surface: CanvasSurface,
    headline: str,
    view: LiveWorkView,
    *,
    speak: SpeakHook,
    work: WorkHook | None = None,
) -> PresentTrace:
    """Sequence a present: speak-headline → swap-to-screen → work → swap-back (AC-CANVAS-11).

    The headline is spoken through the SPEAK delivery authority (the sole speak path,
    AC-XCUT-04) and only THEN does the pure-rendering :class:`~transport.canvas.CanvasSurface`
    swap to the screen — so the projector never speaks. The headline precedes the swap and
    the swap-back follows the work, so the two streams never overlap; the ordered trace is
    returned for the oracle.
    """
    trigger = SwapTrigger(source="present", reason="present_work_on_screen")
    steps: list[str] = []
    await speak(headline)  # sole delivery authority — NOT the projector (AC-XCUT-04)
    steps.append(STEP_HEADLINE)
    await surface.promote(view, trigger=trigger)
    steps.append(STEP_SWAP_TO_SCREEN)
    # The swap-back ALWAYS follows the promote — even if the work raises — so the screen
    # never stays stuck on the promoted view and the tile/screen surfaces stay balanced
    # (AC-CANVAS-11 / AC-CANVAS-09 mutual exclusion; the human-control swap-back is not
    # contingent on the work succeeding). Any work error still propagates after the demote.
    try:
        if work is not None:
            await work(surface)
        steps.append(STEP_WORK)
    finally:
        await surface.demote(trigger=trigger)
        steps.append(STEP_SWAP_BACK)
    return PresentTrace(steps=tuple(steps))


def user_visible_strings() -> dict[str, str]:
    """Every user-visible transport string, for the naming lint (AC-XCUT-01).

    Covers the full disconnect-gap surface: the on-rejoin gap line AND both honest
    terminal-stop announcements (second-drop / rejoin-failed), so no user-visible
    disconnect-gap wording escapes the scan.
    """
    strings: dict[str, str] = {
        "consent_notice": consent_notice(),
        "voice_down_notice": _VOICE_DOWN_NOTICE,
        "gap_line": Gap(dropped_ts=843.0, rejoined_ts=884.0).line(),
        "honest_stop_second_drop": HONEST_STOP_SECOND_DROP,
        "honest_stop_rejoin_failed": HONEST_STOP_REJOIN_FAILED,
    }
    for i, ack in enumerate(CANNED_ACKS):
        strings[f"canned_ack_{i}"] = ack
    return strings
