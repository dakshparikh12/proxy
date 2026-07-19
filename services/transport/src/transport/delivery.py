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

from .consent import consent_notice
from .failure import _VOICE_DOWN_NOTICE, Gap
from .speak import CANNED_ACKS

#: The sole delivery verbs — the only emitters of any channel output (AC-XCUT-04).
DELIVERY_VERBS: frozenset[str] = frozenset({"speak", "send_chat", "show_screen"})


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


def user_visible_strings() -> dict[str, str]:
    """Every user-visible transport string, for the naming lint (AC-XCUT-01).

    A representative gap line is included so the disconnect-gap wording is scanned too.
    """
    strings: dict[str, str] = {
        "consent_notice": consent_notice(),
        "voice_down_notice": _VOICE_DOWN_NOTICE,
        "gap_line": Gap(dropped_ts=843.0, rejoined_ts=884.0).line(),
    }
    for i, ack in enumerate(CANNED_ACKS):
        strings[f"canned_ack_{i}"] = ack
    return strings
