"""Shared Scribe injection seams — one canonical definition per structural type.

The Scribe layer injects its external-call funnel as a structural ``Protocol`` so the
pure ordering/composition logic stays testable with a stub and the concrete funnel is
the single ``libs.http.call_external`` seam (CANONICAL §11.12: every external call is
wrapped with retry + cost telemetry through that one seam). Four Scribe modules
(``call``, ``close``, ``rolling_summary``, ``quality_gate``) previously each re-declared
an identical ``CallExternal`` Protocol; this module hoists the ONE definition they all
import (DRY, behaviour-preserving).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

_T = TypeVar("_T")


class CallExternal(Protocol):
    """Structural type of ``libs.http.call_external`` — the sole external-call seam.

    The concrete funnel returns an ``ExternalCallOutcome`` (value + attempts +
    total_cost_usd); callers read only its ``value`` (duck-typed), so the return is
    typed ``Any``.
    """

    async def __call__(
        self,
        op: Callable[[], Awaitable[_T]],
        *,
        service: str,
        unit_cost_usd: float = 0.0,
    ) -> Any: ...


__all__ = ["CallExternal"]
