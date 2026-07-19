"""Boundary-source resolution — the confirm-at-build contingency (AC-TURN-16, §3.9-7).

Turn-taking needs a natural end-of-turn *boundary* signal. The default source is
AssemblyAI's ``end_of_turn`` field, already on the paid STT stream (§3.6) — one fewer
model to run (CANONICAL §12.11 keeps Smart Turn v3 out of core). But that depends on
Recall's passthrough actually *forwarding* the field, which is a build-time unknown.

This module resolves that contingency **by a build probe, not a guess**: if the probe
sees ``end_of_turn`` on the passthrough, the boundary source stays AAI; if it does not,
Smart Turn v3 is wired as the fallback boundary source. Either way the boundary source
is resolved (``boundary_source_unresolved`` == 0).
"""
from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .wire import has_end_of_turn


class BoundarySource(enum.Enum):
    """Which mechanism supplies the ``boundary(now)`` signal."""

    AAI_END_OF_TURN = "aai_end_of_turn"
    SMART_TURN_V3 = "smart_turn_v3"


@dataclass(frozen=True)
class BoundaryDecision:
    """The resolved boundary source + why — recorded as build evidence."""

    source: BoundarySource
    end_of_turn_forwarded: bool
    reason: str


def resolve_boundary_source(probe_messages: Iterable[dict[str, Any]]) -> BoundaryDecision:
    """Resolve the boundary source from the build-time passthrough probe.

    ``probe_messages`` are sample passthrough messages captured against the live vendor
    wire at build. If any carries ``end_of_turn`` we keep AAI as the boundary source and
    exclude Smart Turn v3 from core; otherwise we wire the Smart Turn v3 fallback.
    """
    forwarded = any(has_end_of_turn(m) for m in probe_messages)
    if forwarded:
        return BoundaryDecision(
            source=BoundarySource.AAI_END_OF_TURN,
            end_of_turn_forwarded=True,
            reason="Recall passthrough forwards AAI end_of_turn; Smart Turn v3 stays excluded (CANONICAL §12.11)",
        )
    return BoundaryDecision(
        source=BoundarySource.SMART_TURN_V3,
        end_of_turn_forwarded=False,
        reason="Recall passthrough does not forward end_of_turn; Smart Turn v3 re-added as boundary source (§3.9-7)",
    )
