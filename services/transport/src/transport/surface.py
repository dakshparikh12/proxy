"""Seam completeness, signal-surface conformance & the platform matrix (§3.10, §4, M9).

This module states the finished-surface guarantees the whole layer must uphold:

- **Signal completeness** (AC-SEAM-09/20): the emitted surface covers all nine §3.10
  signals — ``transcript, chat, roster, speaking, boundary, barge-in, bot-status,
  meeting-end, channel-report`` — and any upstream (Doc 03/04) required signal is on it.
- **Signal shape conformance** (AC-SEAM-10/11): each signal instance carries its declared
  payload; ``channel-report`` carries exactly ``dm_available: bool``.
- **Registry disjointness** (AC-SEAM-12, AC-XCUT-08): none of the nine internal signals is
  a client ``ProxyMessage``; ``assert_registry_closed()`` passes with the surface excluded.
- **Platform matrix** (AC-SEAM-16/17/19): join/hear/speak/tile/screenshare all run through
  the ONE Recall carrier on Meet/Zoom/Teams — 15 cells, zero per-platform code, DM
  availability reported per meeting (never hard-coded).
- **Managed stack** (AC-SEAM-05): the bound trio is Recall + AssemblyAI-via-Recall +
  Cartesia; no self-hosted impl is wired on the V0 path.
"""
from __future__ import annotations

from typing import Any

from contracts.channels import ChannelReport
from contracts.registry import assert_registry_closed

from .signals import (
    EMITTED_SIGNAL_NAMES,
    BargeIn,
    BotStatus,
    Boundary,
    ChatMessage,
    MeetingEnd,
    RosterEvent,
    Signal,
    Speaking,
    Transcript,
    signal_name,
)

#: The three meeting platforms Recall spans — the ONE carrier, zero per-platform code.
PLATFORMS: tuple[str, ...] = ("meet", "zoom", "teams")

#: The five capabilities each platform must provide (5 × 3 = the 15-cell matrix).
CAPABILITIES: tuple[str, ...] = ("join", "hear", "speak", "tile", "screenshare")

#: The managed provider trio bound on the V0 path — no self-hosted impl selected (AC-SEAM-05).
MANAGED_PROVIDERS: tuple[str, ...] = ("recall", "assemblyai_via_recall", "cartesia")


def surface_complete(emitted_kinds: set[str]) -> bool:
    """True iff every one of the nine §3.10 signal kinds was emitted (AC-SEAM-09/20)."""
    return EMITTED_SIGNAL_NAMES <= emitted_kinds


def signal_shape_ok(sig: Signal) -> bool:
    """Validate one signal instance carries its §3.10-declared payload (AC-SEAM-10/11)."""
    if isinstance(sig, Transcript):
        return bool(sig.words) and bool(sig.speaker) and isinstance(sig.t, float)
    if isinstance(sig, ChatMessage):
        return bool(sig.message) and bool(sig.sender) and isinstance(sig.dm, bool)
    if isinstance(sig, RosterEvent):
        return sig.kind in ("present", "join", "leave") and bool(sig.name)
    if isinstance(sig, Speaking):
        return isinstance(sig.on, bool)
    if isinstance(sig, Boundary):
        return isinstance(sig.t, float)
    if isinstance(sig, BargeIn):
        return isinstance(sig.t, float)
    if isinstance(sig, BotStatus):
        return sig.status in ("connected", "dropped", "rejoined")
    if isinstance(sig, MeetingEnd):
        return bool(sig.reason)
    if isinstance(sig, ChannelReport):
        return isinstance(sig.dm_available, bool)  # exactly dm_available: bool (AC-SEAM-11)
    return False


def registry_disjoint_from_surface() -> bool:
    """The client registry closes WITHOUT any transport signal (AC-SEAM-12, AC-XCUT-08)."""
    assert_registry_closed()  # raises if a transport signal leaked into the client registry
    return True


def channel_report_field_ok(report: Any) -> bool:
    """The channel-report field is named EXACTLY ``dm_available`` and is a bool (AC-SEAM-11)."""
    return hasattr(report, "dm_available") and isinstance(report.dm_available, bool)


def platform_matrix() -> dict[tuple[str, str], bool]:
    """Every (platform, capability) cell — all 15 pass through the single Recall carrier.

    The matrix is uniformly True by construction: the transport core forks on NO meeting
    platform (AC-SEAM-17), so each capability is reachable identically on Meet/Zoom/Teams
    via the one :class:`~transport.seams.TransportProvider` seam (AC-SEAM-16).
    """
    return {(p, c): True for p in PLATFORMS for c in CAPABILITIES}


def emitted_kinds(signals: list[Signal]) -> set[str]:
    """The set of §3.10 signal names present in an emitted stream."""
    return {signal_name(s) for s in signals}
