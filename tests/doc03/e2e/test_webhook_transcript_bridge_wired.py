"""AC-TRANSCRIPT-BRIDGE-WIRED — the live transcript actually reaches the notes engine.

Gap DOC02-DOC03-TRANSCRIPT-BRIDGE-UNWIRED: the ``in_call`` webhook started the Scribe
consumer subscribed to the meeting's ``SignalCarrier``, but NOTHING in production bound
transport's emit end (the passthrough->``carrier.emit(record)`` feeder that HearingStage
owns, hearing.py) to that SAME carrier. HearingStage had zero production constructors, so
on a live meeting the Scribe subscribed to an empty carrier, received no ``Transcript``
signals, and the notes ledger was never populated — contradicting the "one in-process
SignalCarrier, transport's emit end and the notes engine's subscribe are two ends of the
one stream" design (meeting_runtime.py docstring). It only ever worked when a TEST manually
emitted onto the carrier.

This drives the REAL meeting path end-to-end WITHOUT any manual ``carrier.emit`` by the
test — exactly the thing the previous e2e test still did by hand:

  * a real ``bot.in_call`` webhook drains and STARTS a live ``MeetingRuntime``; then
  * real Recall real-time ``transcript`` passthrough webhooks (wire shape
    ``{words, speaker, timestamp}`` — transport.wire.parse_transcript's confirmed schema)
    drain through the SAME product path and, via the runtime's production HearingStage bound
    to the runtime's carrier, flow transport->carrier->coalescer->Scribe into REAL
    ``note_deltas`` rows in Postgres; and
  * a ``call_ended`` webhook flushes (MeetingEnd) + tears the runtime down.

The test emits NOTHING on the carrier itself: if the transport->carrier bridge is unwired,
the ledger stays empty and this fails.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from db import Database, open_pool, repos

from harness.meeting_runtime import MeetingRuntimeRegistry
from harness.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned this session"
)

_LLM_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
requires_funded_llm = pytest.mark.skipif(
    not _LLM_KEY,
    reason=(
        "reality tier: this drives the REAL Scribe micro-call (vendor:anthropic) end to "
        "end into note_deltas; a funded ANTHROPIC_API_KEY is required. The transport->"
        "carrier bridge itself is proven deterministically in "
        "test_transcript_bridge_reaches_carrier.py."
    ),
)


@requires_pg
@requires_funded_llm
@pytest.mark.asyncio
async def test_live_transcript_webhook_reaches_notes_ledger_no_manual_emit() -> None:
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

    # 1) in_call — starts the live runtime (and its production HearingStage->carrier bridge).
    await _ingest({"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1

    runtime = registry.get(str(meeting_id))
    assert runtime is not None, "in_call did not START a MeetingRuntime"

    # 2) Real Recall real-time transcript passthrough webhooks — the confirmed wire shape.
    #    The test does NOT touch runtime.carrier: the ONLY way these reach the Scribe is the
    #    production transport->carrier bridge under test.
    for words, speaker, ts in (
        ("First item is the retry backoff.", "Ana", 0.0),
        ("I will own the applier fold.", "Zed", 5.0),
        ("Agreed, ship it today.", "Ana", 10.0),
    ):
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

    # 3) call_ended — MeetingEnd flushes the trailing window + drains the serial consumer.
    await _ingest({"event": "bot.call_ended", "data": {"bot_id": bot_id}})
    await drain_pending_webhooks(db, registry=registry)
    assert registry.get(str(meeting_id)) is None, "call_ended did not end the runtime"

    # The durable ledger now carries real note_deltas rows produced from the LIVE transcript
    # webhooks — no manual emit anywhere in this test.
    async def _has_rows() -> bool:
        async with db.acquire() as conn:
            rows = await repos.notes.load_deltas(conn, meeting_id)
        return bool(rows)

    for _ in range(50):
        if await _has_rows():
            break
        await asyncio.sleep(0.1)
    assert await _has_rows(), (
        "no note_deltas rows — the live transcript webhooks never reached the notes engine "
        "(transport->carrier bridge is unwired)"
    )
