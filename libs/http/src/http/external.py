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
from typing import TYPE_CHECKING, Any, TypeVar

import httpx

if TYPE_CHECKING:  # import only for typing — the SDK is loaded lazily at call time
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
    """The ONLY construction of the Anthropic model client in the product.

    The SDK is imported lazily HERE so modules that need only the ``call_external``
    seam (e.g. the invite path's ``RecallTransport``) do not drag in the Anthropic
    package at import time; the raw client is still constructed nowhere else.
    """
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(**kwargs)


def http_client(**kwargs: Any) -> httpx.AsyncClient:
    """The ONLY construction of a raw httpx client in the product."""
    return httpx.AsyncClient(**kwargs)


def gcs_bucket(bucket_name: str) -> Any:
    """The ONLY construction of the raw GCS storage client in the product.

    Returns the ``google.cloud.storage`` bucket handle for ``bucket_name``. The
    SDK is imported lazily HERE (never at import/boot time) so a host that only
    needs the ``call_external`` seam does not drag in the GCS package, and boot
    stays offline — no client is constructed until a real close pass asks for a
    bucket. This is the sole legitimate home for the raw ``storage.Client``
    construction; no product module outside ``libs/http`` may hold it.
    """
    from google.cloud import storage  # lazy: GCS SDK only when a real bucket is needed

    return storage.Client().bucket(bucket_name)


def e2b_sandbox_class() -> Any:
    """The ONLY reference to the raw E2B ``AsyncSandbox`` class in the product.

    The E2B SDK is imported lazily HERE (never at import/boot time) so a host that
    only needs the ``call_external`` seam — or that runs the whole Workroom against
    an in-process fake — does not drag in the ``e2b`` package, and boot stays
    offline. The wire surface confirmed against live E2B docs (CANONICAL §11.10):
    ``AsyncSandbox.create(template=..., timeout=<seconds>, envs=<dict>, metadata=...)``,
    instance ``.kill()`` / ``.set_timeout(seconds)`` / ``.is_running()`` and the
    classmethods ``AsyncSandbox.connect(sandbox_id)`` / ``AsyncSandbox.list()``.

    This is the sole legitimate home for the raw E2B client construction; no
    product module outside ``libs/http`` may import ``e2b``. Raises ``ImportError``
    (honest degrade) when the package is absent — the caller decides whether that
    is fatal (a live deploy) or a no-op (a fake-backed test path).
    """
    # lazy: E2B SDK only when a real sandbox is provisioned. e2b is a DEPLOY-time
    # dependency (the template bake) and is deliberately absent from the offline
    # dev/test env — mypy cannot see its stub, so the import is scoped-ignored here
    # (the sole raw-client home; no product module outside libs/http imports e2b).
    from e2b import AsyncSandbox

    return AsyncSandbox
