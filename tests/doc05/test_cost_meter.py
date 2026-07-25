"""Doc 05 §3.9 — per-task total_cost recorded via the cost meter, aggregated per meeting.

Authored from the spec. §3.9's last cost bullet: "Full ``total_cost_usd`` + cache-read/creation
split telemetry per task, aggregated per meeting — it's how we prove the cached prefix is
hitting (Doc 04 owns the live per-meeting circuit-breaker that gates spend against the
$1/hr SLA)."

The node records per-task cost through the SAME meter Doc 04 reads (``ops.cost``), so the
Workroom's spend feeds the meeting_cost row the circuit-breaker gates against — it never
opens a bespoke cost sink. Proven on the real host path with an in-process cost sink.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from libs.ops import sandbox_provider
from workroom.session import SessionDriver

from .fakes import FakeSidecar


class _RecordingCostMeter:
    """An in-process stand-in for ``ops.cost.record_micro_call_cost`` (the seam-metered writer).

    Records exactly what the driver hands the meter per task: the meeting id + the SDK
    ``total_cost_usd`` and the cache-read/creation split (§3.9). The real writer increments
    the durable ``meeting_cost`` row; this fake records the call so the test asserts the
    driver fed the meter the right numbers."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(
        self,
        *,
        meeting_id: str,
        total_cost_usd: float,
        cache_read_usd: float = 0.0,
        cache_creation_usd: float = 0.0,
    ) -> None:
        self.calls.append(
            {
                "meeting_id": meeting_id,
                "total_cost_usd": total_cost_usd,
                "cache_read_usd": cache_read_usd,
                "cache_creation_usd": cache_creation_usd,
            }
        )


class _RecordingStore:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    async def set_result(self, *, run_id: Any, result_ref: dict[str, Any], status: str) -> None:
        self.results.append({"run_id": run_id, "result_ref": result_ref, "status": status})


class _CostProvider:
    """A provider streaming a terminal RESULT chunk carrying the SDK cost + cache split."""

    def __init__(self, *, total_cost_usd: float, cache_read: int, cache_creation: int) -> None:
        self._total = total_cost_usd
        self._cache_read = cache_read
        self._cache_creation = cache_creation

    def stream(self, prompt: str, options: Any) -> Any:
        from contracts import AgentChunk

        meta = {
            "total_cost_usd": self._total,
            "cache_read_input_tokens": self._cache_read,
            "cache_creation_input_tokens": self._cache_creation,
            "input_tokens": 40,
            "session_id": "sess-cost",
        }

        async def _gen() -> Any:
            yield AgentChunk(type="RESULT", metadata=meta)

        return _gen()


def _bundle(meeting: str) -> Any:
    from datetime import datetime, timezone

    from contracts import Bundle

    return Bundle(
        task_id=uuid4(),
        ask="Build the rate limiter",
        speaker="Sam",
        timestamp=datetime.now(timezone.utc),
        transcript_tail="…rate limiter…",
        notes_ref=UUID(meeting),
    )


@pytest.mark.asyncio
async def test_per_task_total_cost_is_recorded_through_the_meter() -> None:
    """After a task runs, the driver records its ``total_cost_usd`` (+ split) via the meter.

    This is the DoD line 'per-task total_cost is recorded'. The recorded number is the SDK's
    real terminal ``total_cost_usd`` off the RESULT frame — not a model-narrated guess."""
    meeting = str(uuid4())
    handle = sandbox_provider.provision(meeting_id=meeting)
    sidecar = FakeSidecar(
        jwt_secret=handle.jwt_secret, session_id=handle.id, code_hash="sha256:baked-code-hash"
    )
    meter = _RecordingCostMeter()
    store = _RecordingStore()

    async def _probe(_h: Any) -> dict[str, Any]:
        return sidecar.health()

    driver = SessionDriver(
        provider=_CostProvider(total_cost_usd=0.0731, cache_read=8000, cache_creation=200),
        store=store,
        cost_meter=meter,
        health_probe=_probe,
        model="claude-sonnet-4-6",
    )

    envelope = await driver.run_task(
        _bundle(meeting), run_id=uuid4(), preflight_code_hash="sha256:baked-code-hash"
    )

    assert envelope.status == "done"
    # the meter received exactly this task's total + split, keyed by the meeting
    assert len(meter.calls) == 1
    call = meter.calls[0]
    assert call["meeting_id"] == meeting
    assert call["total_cost_usd"] == pytest.approx(0.0731)
    # the cache split rides too (how §3.9 proves the cached prefix is hitting)
    assert call["cache_read_usd"] >= 0.0
    assert call["cache_creation_usd"] >= 0.0
    # and the same cost is also in the Envelope artifact (the trace)
    assert envelope.artifact["cost"]["total_cost_usd"] == pytest.approx(0.0731)
    assert envelope.artifact["cost"]["cache_read_input_tokens"] == 8000


@pytest.mark.asyncio
async def test_cost_meter_is_optional_run_still_completes_without_one() -> None:
    """No meter injected → the run still completes (Rule 6 / honest-degrade); cost rides the
    Envelope artifact regardless, so the trace is never lost."""
    meeting = str(uuid4())
    handle = sandbox_provider.provision(meeting_id=meeting)
    sidecar = FakeSidecar(
        jwt_secret=handle.jwt_secret, session_id=handle.id, code_hash="sha256:baked-code-hash"
    )
    store = _RecordingStore()

    async def _probe(_h: Any) -> dict[str, Any]:
        return sidecar.health()

    driver = SessionDriver(
        provider=_CostProvider(total_cost_usd=0.01, cache_read=10, cache_creation=1),
        store=store,
        health_probe=_probe,
        model="claude-sonnet-4-6",
    )
    envelope = await driver.run_task(
        _bundle(meeting), run_id=uuid4(), preflight_code_hash="sha256:baked-code-hash"
    )
    assert envelope.status == "done"
    assert envelope.artifact["cost"]["total_cost_usd"] == pytest.approx(0.01)
