"""AC-TRANSCRIPT-BRIDGE-WIRED (deterministic) — transport's emit end reaches the carrier.

Gap DOC02-DOC03-TRANSCRIPT-BRIDGE-UNWIRED: on a live meeting the Scribe subscribed to a
fresh ``SignalCarrier`` but NOTHING in production bound transport's emit end (the
passthrough->``carrier.emit(record)`` feeder that ``HearingStage`` owns) to that SAME
carrier — so the notes engine received no ``Transcript`` signals and the ledger stayed
empty. It only worked when a TEST manually emitted onto the carrier.

This proves the bridge on the REAL product path WITHOUT the vendor LLM (the serial Scribe
consumer's micro-call needs a funded Anthropic key — an env boundary, not the bridge). A
subscriber attaches to the runtime's carrier BESIDE the Scribe and asserts that real Recall
real-time ``transcript`` passthrough webhooks, drained through
``harness.webhooks.drain_pending_webhooks``, arrive as the exact ``Transcript`` signals on
that carrier — with NO manual ``carrier.emit`` anywhere. If the transport->carrier bridge is
unwired, no signal arrives and this fails.

The full LLM-backed leg (transcript -> note_deltas rows) is covered by
``test_webhook_transcript_bridge_wired.py`` (reality tier; runs when a key is funded).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from db import Database, open_pool, repos
from transport.signals import Transcript

from harness.meeting_runtime import MeetingRuntimeRegistry
from harness.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned this session"
)


@requires_pg
@pytest.mark.asyncio
async def test_transcript_webhook_reaches_carrier_via_real_drain() -> None:
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")
    registry = MeetingRuntimeRegistry(db)

    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id", f"t-{uuid.uuid4().hex[:8]}"
        )
        repo = await conn.fetchrow(
            "INSERT INTO repos (tenant_id, full_name, default_branch) VALUES ($1,$2,$3) RETURNING id",
            tenant["id"], "example/r", "main",
        )
    bot_id = f"recall-bot-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        meeting = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant["id"],
            repo_id=repo["id"],
            meeting_url="https://meet.example/abc",
            pinned_sha="deadbeef",
            recall_bot_id=bot_id,
            status="live",
        )
    meeting_id = meeting["id"]

    async def _ingest(payload: dict) -> None:
        guid = f"wh-{uuid.uuid4().hex}"
        async with db.acquire() as conn:
            await repos.webhooks.insert_event(conn, guid, payload)

    # in_call — the real join path starts the runtime (and its HearingStage->carrier bridge).
    await _ingest({"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1
    runtime = registry.get(str(meeting_id))
    assert runtime is not None, "in_call did not START a MeetingRuntime"

    # Attach a probe subscriber to the SAME carrier the Scribe consumes (beside it).
    received: list[Transcript] = []
    stream = runtime.carrier.subscribe()

    async def _collect() -> None:
        async for signal in stream:
            if isinstance(signal, Transcript):
                received.append(signal)

    collector = asyncio.ensure_future(_collect())

    expected = [
        ("First item is the retry backoff.", "Ana", 0.0),
        ("I will own the applier fold.", "Zed", 5.0),
        ("Agreed, ship it today.", "Ana", 10.0),
    ]
    for words, speaker, ts in expected:
        await _ingest(
            {
                "event": "transcript.data",
                "data": {
                    "bot_id": bot_id,
                    "words": words,
                    "speaker": speaker,
                    "timestamp": ts,
                    "end_of_turn": True,
                },
            }
        )
    assert await drain_pending_webhooks(db, registry=registry) >= 3

    # The probe received the exact per-speaker records — driven ONLY by the webhook drain
    # through the production transport->carrier bridge (no manual emit in this test).
    for _ in range(50):
        if len(received) >= 3:
            break
        await asyncio.sleep(0.02)
    collector.cancel()

    assert [(r.words, r.speaker, r.t) for r in received] == expected, (
        f"transport->carrier bridge did not deliver the live transcript records: {received!r}"
    )

    await registry.end_meeting(str(meeting_id))


@requires_pg
@pytest.mark.asyncio
async def test_drifted_transcript_webhook_drains_without_poisoning() -> None:
    """A drifted passthrough message fails LOUD (logged) but the drain still marks the row
    processed — one bad vendor message never deadlocks the webhook queue (never a poison
    row), CANONICAL §11.10 fail-loud reconciled with the drain's never-poison contract.
    """
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")
    registry = MeetingRuntimeRegistry(db)

    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id", f"t-{uuid.uuid4().hex[:8]}"
        )
        repo = await conn.fetchrow(
            "INSERT INTO repos (tenant_id, full_name, default_branch) VALUES ($1,$2,$3) RETURNING id",
            tenant["id"], "example/r", "main",
        )
    bot_id = f"recall-bot-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        meeting = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant["id"],
            repo_id=repo["id"],
            meeting_url="https://meet.example/abc",
            pinned_sha="deadbeef",
            recall_bot_id=bot_id,
            status="live",
        )
    meeting_id = meeting["id"]

    async def _ingest(payload: dict) -> None:
        guid = f"wh-{uuid.uuid4().hex}"
        async with db.acquire() as conn:
            await repos.webhooks.insert_event(conn, guid, payload)

    await _ingest({"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1

    # A drifted transcript body (missing the confirmed ``speaker`` field).
    await _ingest(
        {"event": "transcript.data", "data": {"bot_id": bot_id, "words": "hi", "timestamp": 0.0}}
    )
    drained = await drain_pending_webhooks(db, registry=registry)
    assert drained >= 1, "the drifted transcript webhook was not drained"

    # No pending rows left — the drift did not poison the queue.
    async with db.acquire() as conn:
        pending = await repos.webhooks.list_pending(conn)
    assert not any(str(p.get("payload", "")).find(bot_id) >= 0 for p in pending), (
        "drifted transcript webhook left a poison pending row"
    )

    await registry.end_meeting(str(meeting_id))
