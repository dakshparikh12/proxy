"""Doc 05 §3.9 / §3.13-step-9 — the code-hash ``/health`` preflight (fail fast, no cold-start).

Authored from the spec (no sealed doc05 bundle). The preflight is the §3.9 "cheap insurance"
before an expensive run: a fast ``GET /health`` (sandbox + MCP up, clone OK, code-hash matches
expected) that FAILS FAST with a clear reason. Its whole purpose: "our worst in-meeting failure
is burning meeting-time against a stale/expired sandbox and failing late" — so a cold-start
NEVER happens on the live tier, and ``run_task`` refuses BEFORE it ever builds a ``query()``.

For a quick ask the preflight shrinks to an in-process "sandbox healthy?" flag (their 10s MCP
preflight is too slow for the hot loop; keep the pattern, not the latency, §3.9).

Every test drives the REAL host preflight on the ``SessionDriver`` against the in-process fakes
(the real E2B sidecar is a Phase-3 deploy artifact; the host path is proven against fakes).
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from libs.ops import sandbox_provider
from workroom.session import PreflightResult, SessionDriver

from .fakes import FakeSidecar


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #

EXPECTED_HASH = "sha256:baked-code-hash"


class _RecordingStore:
    """An in-process operation_runs sink — records the terminal Envelope + status."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    async def set_result(self, *, run_id: Any, result_ref: dict[str, Any], status: str) -> None:
        self.results.append({"run_id": run_id, "result_ref": result_ref, "status": status})


def _fresh_meeting() -> str:
    """A distinct meeting id per test so the module-level provider maps never collide.

    ``notes_ref`` is a UUID (CANONICAL §11.2); the driver keys the meeting as
    ``str(bundle.notes_ref)``, so the meeting id used to provision must be that same string.
    """
    return str(uuid4())


def _health_probe_from(sidecar: FakeSidecar) -> Any:
    """Build the injectable health-probe seam from a fake sidecar's unauth ``/health``.

    In production this is a fast ``GET https://8081-<sandbox>.e2b.app/health`` through
    ``call_external``; here the fake sidecar answers with the same ``{status, code_hash,
    clone_ready}`` shape."""

    async def _probe(handle: Any) -> dict[str, Any]:
        return sidecar.health()

    return _probe


# --------------------------------------------------------------------------- #
# 1. Happy path — a warm, matching sandbox passes fast and lets the run proceed.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_preflight_passes_on_a_warm_matching_sandbox() -> None:
    """A live sandbox + MCP up + clone ready + matching code-hash → healthy, run may proceed."""
    meeting = _fresh_meeting()
    handle = sandbox_provider.provision(meeting_id=meeting)
    sidecar = FakeSidecar(jwt_secret=handle.jwt_secret, session_id=handle.id, code_hash=EXPECTED_HASH)
    driver = SessionDriver(health_probe=_health_probe_from(sidecar))

    result = await driver.preflight(meeting_id=meeting, expected_code_hash=EXPECTED_HASH)

    assert isinstance(result, PreflightResult)
    assert result.healthy is True
    assert result.reason is None


# --------------------------------------------------------------------------- #
# 2. Fail-fast reasons — each returns healthy=False with a CLEAR, distinct reason.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_preflight_fails_fast_on_an_expired_sandbox_whose_mcp_is_gone() -> None:
    """An expired/reaped sandbox surfaces as an unreachable MCP ``/health`` → fail fast.

    This is the §3.9 headline scenario: the pre-provisioned sandbox got reaped mid-meeting.
    The preflight's code-hash ``/health`` probe against a reaped sandbox can't connect, so the
    preflight fails fast with a clear reason — it never burns meeting-time launching an
    expensive build against a dead sandbox, and never papers over the reap with a silent
    cold-boot on the live tier."""
    meeting = _fresh_meeting()
    sandbox_provider.provision(meeting_id=meeting)

    async def _reaped_probe(_h: Any) -> dict[str, Any]:
        # A reaped sandbox's :8081 sidecar is gone — the GET /health connection refuses.
        raise ConnectionError("sidecar :8081 connection refused (sandbox reaped)")

    driver = SessionDriver(health_probe=_reaped_probe)
    result = await driver.preflight(meeting_id=meeting, expected_code_hash=EXPECTED_HASH)

    assert result.healthy is False
    assert result.reason is not None and result.reason.strip()


@pytest.mark.asyncio
async def test_preflight_fails_fast_on_code_hash_mismatch() -> None:
    """A stale sandbox baked at a different SHA → fail fast with a code-hash-mismatch reason.

    This is the §3.9 headline: never burn meeting-time against a STALE sandbox and fail late.
    """
    meeting = _fresh_meeting()
    handle = sandbox_provider.provision(meeting_id=meeting)
    sidecar = FakeSidecar(jwt_secret=handle.jwt_secret, session_id=handle.id, code_hash="sha256:OLD-stale-hash")
    driver = SessionDriver(health_probe=_health_probe_from(sidecar))

    result = await driver.preflight(meeting_id=meeting, expected_code_hash=EXPECTED_HASH)

    assert result.healthy is False
    assert result.reason is not None
    assert "hash" in result.reason.lower()


@pytest.mark.asyncio
async def test_preflight_fails_fast_when_clone_not_ready() -> None:
    """MCP up but the clone hasn't landed → fail fast (a run would work against an empty tree)."""
    meeting = _fresh_meeting()
    handle = sandbox_provider.provision(meeting_id=meeting)
    sidecar = FakeSidecar(
        jwt_secret=handle.jwt_secret, session_id=handle.id, code_hash=EXPECTED_HASH, clone_ready=False
    )
    driver = SessionDriver(health_probe=_health_probe_from(sidecar))

    result = await driver.preflight(meeting_id=meeting, expected_code_hash=EXPECTED_HASH)

    assert result.healthy is False
    assert result.reason is not None
    assert "clone" in result.reason.lower()


@pytest.mark.asyncio
async def test_preflight_fails_fast_when_mcp_is_down() -> None:
    """The sidecar/MCP probe raises (server down) → fail fast, honest reason, never raises (Rule 6)."""
    meeting = _fresh_meeting()
    handle = sandbox_provider.provision(meeting_id=meeting)

    async def _down_probe(_handle: Any) -> dict[str, Any]:
        raise ConnectionError("sidecar :8081 refused")

    driver = SessionDriver(health_probe=_down_probe)
    result = await driver.preflight(meeting_id=meeting, expected_code_hash=EXPECTED_HASH)

    assert result.healthy is False
    assert result.reason is not None and result.reason.strip()


# --------------------------------------------------------------------------- #
# 3. A big build NEVER launches a query() when the preflight fails (the DoD line).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_run_task_refuses_before_any_query_when_preflight_fails() -> None:
    """When the preflight fails, ``run_task`` returns a failed Envelope WITHOUT driving the
    provider — the cold-start-on-the-live-tier that §3.9 forbids never happens.

    We inject a provider that RAISES if touched; a passing test proves the driver returned
    before it ever reached the provider seam."""
    meeting = _fresh_meeting()
    handle = sandbox_provider.provision(meeting_id=meeting)
    sidecar = FakeSidecar(jwt_secret=handle.jwt_secret, session_id=handle.id, code_hash="sha256:STALE")

    class _ExplodingProvider:
        def stream(self, prompt: str, options: Any) -> Any:
            raise AssertionError("provider was reached despite a failed preflight (cold-start on live tier!)")

    store = _RecordingStore()
    driver = SessionDriver(
        provider=_ExplodingProvider(),
        store=store,
        health_probe=_health_probe_from(sidecar),
    )
    bundle = _make_bundle(meeting)

    envelope = await driver.run_task(
        bundle, run_id=uuid4(), preflight_code_hash=EXPECTED_HASH
    )

    assert envelope.status == "failed"
    assert envelope.receipts  # names the preflight reason, honestly
    # persisted as failed into the SAME operation_runs row (no bespoke table)
    assert store.results and store.results[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_run_task_proceeds_when_preflight_passes() -> None:
    """A passing preflight lets the run reach the provider and produce a real Envelope + cost."""
    meeting = _fresh_meeting()
    handle = sandbox_provider.provision(meeting_id=meeting)
    sidecar = FakeSidecar(jwt_secret=handle.jwt_secret, session_id=handle.id, code_hash=EXPECTED_HASH)
    store = _RecordingStore()
    driver = SessionDriver(
        provider=_FakeProvider(total_cost_usd=0.0123),
        store=store,
        health_probe=_health_probe_from(sidecar),
        model="claude-sonnet-4-6",
    )
    bundle = _make_bundle(meeting)

    envelope = await driver.run_task(bundle, run_id=uuid4(), preflight_code_hash=EXPECTED_HASH)

    assert envelope.status == "done"
    assert envelope.artifact is not None
    assert envelope.artifact["cost"]["total_cost_usd"] == pytest.approx(0.0123)


# --------------------------------------------------------------------------- #
# helpers building a real Bundle + a fake provider streaming a RESULT chunk
# --------------------------------------------------------------------------- #

def _make_bundle(meeting: str) -> Any:
    from datetime import datetime, timezone
    from uuid import UUID

    from contracts import Bundle

    return Bundle(
        task_id=uuid4(),
        ask="Where is the retry logic?",
        speaker="Sam",
        timestamp=datetime.now(timezone.utc),
        transcript_tail="…the retry logic…",
        notes_ref=UUID(meeting),
    )


class _FakeProvider:
    """A minimal provider streaming a single terminal RESULT chunk carrying SDK cost telemetry."""

    def __init__(self, *, total_cost_usd: float) -> None:
        self._total = total_cost_usd

    def stream(self, prompt: str, options: Any) -> Any:
        from contracts import AgentChunk

        total = self._total

        async def _gen() -> Any:
            yield AgentChunk(
                type="RESULT",
                metadata={
                    "total_cost_usd": total,
                    "cache_read_input_tokens": 900,
                    "cache_creation_input_tokens": 100,
                    "input_tokens": 50,
                    "session_id": "sess-1",
                },
            )

        return _gen()
