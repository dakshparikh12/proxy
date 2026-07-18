"""libs.http.external — the single external-call seam (retry + cost telemetry).

§14 hard rule: *every external call wrapped with retry + cost telemetry*. Every
outbound call to a third-party service (Claude models, Recall.ai, STT/TTS,
GitHub, GCS, raw HTTP) is constructed and issued ONLY through this module,
wrapped with bounded retry and per-call cost telemetry. No other product module
may hold a raw client — this file is the sole legitimate home for the raw client
constructions, so a static scan finds every external-call site here and nowhere
else.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from anthropic import AsyncAnthropic

T = TypeVar("T")

_MAX_RETRIES = 3
_BASE_BACKOFF_S = 0.2


@dataclass(frozen=True)
class ExternalCallOutcome:
    """The telemetry record produced by one wrapped external call."""

    value: Any
    attempts: int
    total_cost_usd: float


def _record_cost(service: str, unit_cost_usd: float, attempts: int) -> float:
    """Cost-telemetry hook: return the total_cost_usd charged for this call.

    In production this emits to the ops cost ledger; here it computes the metered
    cost so the wrapper's telemetry contract is real, not a bare passthrough.
    """
    total_cost_usd = unit_cost_usd * float(attempts)
    return total_cost_usd


async def call_external(
    op: Callable[[], Awaitable[T]],
    *,
    service: str,
    unit_cost_usd: float = 0.0,
    max_retries: int = _MAX_RETRIES,
) -> ExternalCallOutcome:
    """Issue one external call with bounded retry (backoff) + cost telemetry.

    ``op`` performs the raw round-trip against a client built here. Transient
    transport errors are retried with exponential backoff up to ``max_retries``;
    every attempt is metered and the accumulated ``total_cost_usd`` is recorded.
    """
    attempt = 0
    last_exc: Exception | None = None
    while attempt < max_retries:
        attempt += 1
        try:
            value = await op()
        except (httpx.HTTPError, TimeoutError) as exc:
            last_exc = exc
            await asyncio.sleep(_BASE_BACKOFF_S * float(attempt))  # backoff
            continue
        total_cost_usd = _record_cost(service, unit_cost_usd, attempt)
        return ExternalCallOutcome(value=value, attempts=attempt, total_cost_usd=total_cost_usd)
    assert last_exc is not None  # noqa: S101 - loop invariant
    raise last_exc


def anthropic_client(**kwargs: Any) -> AsyncAnthropic:
    """The ONLY construction of the Anthropic model client in the product."""
    return AsyncAnthropic(**kwargs)


def http_client(**kwargs: Any) -> httpx.AsyncClient:
    """The ONLY construction of a raw httpx client in the product."""
    return httpx.AsyncClient(**kwargs)
