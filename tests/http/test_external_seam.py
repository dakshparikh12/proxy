"""libs.http.external — the single external-call seam's resilience to TRANSPORT-induced cancels.

Regression: the WS6 long-session certification crashed a whole ``run_ask`` on long-wake[4] when a
single E2B files.read poll surfaced a bare ``asyncio.CancelledError`` — the E2B/httpx/anyio stack
converts an HTTP/2 stream-reset / GOAWAY under load into a ``CancelledError`` at the await point.
``call_external`` retries ``httpx.HTTPError``/``TimeoutError`` but let ``CancelledError`` propagate,
so one transient transport blip during a wake poll took down the meeting.

The generalizable fix (physics of the transport, Law 4 — not a situation→action rule): a
``CancelledError`` from ``await op()`` when THIS task is not itself being cancelled by a caller
(``current_task().cancelling() == 0``) is a transport blip → retry with backoff like any transient;
a GENUINE caller cancellation (``cancelling() > 0``, e.g. meeting-end drain) is honored immediately
(re-raised, never retried). Applied once at the seam so EVERY external round-trip (E2B read/write/
provision, model, GCS, HTTP) is uniformly resilient.
"""
from __future__ import annotations

import asyncio

import pytest

from libs.http.src.http.external import call_external


@pytest.mark.asyncio
async def test_transport_cancel_is_retried_not_propagated() -> None:
    """A bare CancelledError from the op (transport reset, this task NOT cancelled) is retried, and
    a later success returns normally — one blip must not kill the call."""
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise asyncio.CancelledError("http2 stream reset")  # transport-induced, not a real cancel
        return "ok"

    out = await call_external(flaky, service="e2b")
    assert out.value == "ok"
    assert calls["n"] == 2  # retried once, then succeeded


@pytest.mark.asyncio
async def test_transport_cancel_exhausts_retries_then_honestly_raises() -> None:
    """A persistent transport cancel still terminates (bounded retry) rather than looping forever —
    it re-raises after the budget, so the caller degrades honestly (never a silent hang)."""
    async def always_cancel() -> str:
        raise asyncio.CancelledError("persistent reset")

    with pytest.raises(asyncio.CancelledError):
        await call_external(always_cancel, service="e2b", max_retries=3)


@pytest.mark.asyncio
async def test_genuine_caller_cancellation_is_honored_immediately() -> None:
    """When a CALLER genuinely cancels this task (meeting-end drain), the seam must NOT swallow or
    retry it — it propagates at once so shutdown is prompt. Detected via current_task().cancelling()."""
    started = asyncio.Event()
    attempts = {"n": 0}

    async def slow() -> str:
        attempts["n"] += 1
        started.set()
        await asyncio.sleep(10)  # will be interrupted by the outer .cancel()
        return "never"

    async def runner() -> None:
        await call_external(slow, service="e2b")

    task = asyncio.create_task(runner())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The op was entered once and the genuine cancel was honored — NOT retried into a second attempt.
    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_httpx_error_still_retried() -> None:
    """The pre-existing transient behavior is preserved: an httpx transport error still retries."""
    import httpx

    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("boom")
        return "ok"

    out = await call_external(flaky, service="e2b")
    assert out.value == "ok"
    assert calls["n"] == 2
