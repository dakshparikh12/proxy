"""The injected ``call_external`` seam (AC-XCUT-03, AGENTS §"External calls").

Every Recall / AssemblyAI / Cartesia round-trip in this layer is issued through the
single ``libs.http.call_external`` funnel (retry + cost telemetry). Transport itself
holds **no raw provider client and imports no provider SDK** — the ``meeting_runtime``
harness injects the real seam at wire-up, so a static scan finds every external-call
site routed through ``call_external`` and no raw client anywhere in ``services/transport``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


class CallExternal(Protocol):
    """Structural type of ``libs.http.call_external`` — the sole external-call seam.

    The concrete funnel returns an ``ExternalCallOutcome`` (value + attempts +
    total_cost_usd); callers here only need its ``value``, so the return is ``Any``.
    """

    async def __call__(
        self,
        op: Callable[[], Awaitable[T]],
        *,
        service: str,
        unit_cost_usd: float = 0.0,
    ) -> Any: ...
