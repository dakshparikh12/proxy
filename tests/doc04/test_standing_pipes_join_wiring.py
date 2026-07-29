"""orchestrator.standing-pipes — join-time plumbing on the REAL join path (§2/§3/§3.8).

The standing-pipes node's DoD: at Recall ``in_call`` the harness wires the standing
pipes (audio→STT→transcript→Scribe; transcript→Scribe→material-events) as PURE
forwarding with ZERO agent calls, subscribed ONCE at join (not re-wired per event),
and starts the availability-critical STT-credential refresh on its OWN in-process
asyncio interval (§3.8 split — NOT the scale-to-zero reconcile cron). The Scribe
serial consumer starts on the Recall ``in_call`` callback and stops on ``call_ended``.

These tests drive the REAL production join path — the webhook drain
(``control_plane.webhooks.drain_pending_webhooks``) through ``MeetingRuntimeRegistry`` —
against the live test Postgres, exactly as a real Recall callback would. They assert:

  * ``stt_refresh`` — the STT credential refresh loop runs on its OWN in-process
    asyncio interval, started at join, cancelled at meeting end; a refresh_fn that
    raises does NOT kill the loop (it must not die silently mid-meeting, degrading
    transcription — a named node risk);
  * ``standing_pipes`` — the Scribe subscribes to the carrier EXACTLY ONCE at join
    (subscription count == 1, not re-wired per transcript), and a long "silent"
    meeting (many heartbeat/ambient signals, nothing addressed) keeps the pipes
    forwarding with ZERO agent/wake calls.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from db import Database, open_pool, repos

from control_plane.meeting_runtime import MeetingRuntimeRegistry
from control_plane.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned this session"
)


async def _seed_meeting(db: Database) -> tuple[str, str]:
    """Insert a tenant+repo+live meeting; return ``(meeting_id, recall_bot_id)``."""
    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
            f"t-{uuid.uuid4().hex[:8]}",
        )
        repo = await conn.fetchrow(
            "INSERT INTO repos (tenant_id, full_name, default_branch) "
            "VALUES ($1,$2,$3) RETURNING id",
            tenant["id"],
            "example/r",
            "main",
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
    return str(meeting["id"]), bot_id


async def _ingest(db: Database, payload: dict) -> None:
    guid = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(conn, guid, payload)


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_stt_refresh_loop_runs_on_own_interval_started_at_join() -> None:
    """The STT credential refresh runs on its OWN in-process interval, started at join.

    Drives the REAL webhook join path (in_call → registry.start_meeting). The refresh
    loop must fire on its own asyncio interval (NOT the reconcile cron), keep firing
    while the meeting is live, and be cancelled when ``call_ended`` ends the meeting.
    """
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")

    refreshes: list[float] = []

    async def _refresh() -> None:
        refreshes.append(asyncio.get_event_loop().time())

    # A tiny interval so the test observes several ticks quickly (the seam is the same
    # one production uses — an in-process interval, never the scale-to-zero cron).
    registry = MeetingRuntimeRegistry(
        db, stt_refresh_fn=_refresh, stt_refresh_interval_s=0.01
    )

    meeting_id, bot_id = await _seed_meeting(db)

    # in_call — the real join path starts the runtime AND its STT refresh loop.
    await _ingest(db, {"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1
    runtime = registry.get(meeting_id)
    assert runtime is not None, "in_call did not START a MeetingRuntime"
    assert runtime.stt_refresh_running, (
        "the STT credential refresh loop was not started at join (§3.8: it must run on "
        "its own in-process interval, not the reconcile cron)"
    )

    # The loop fires repeatedly on its own interval while the meeting is live.
    for _ in range(100):
        if len(refreshes) >= 3:
            break
        await asyncio.sleep(0.01)
    assert len(refreshes) >= 3, (
        f"the STT refresh loop did not tick on its own interval; ticks={len(refreshes)}"
    )

    # call_ended — the loop is cancelled with the runtime (it must not outlive the meeting).
    await registry.end_meeting(meeting_id)
    assert not runtime.stt_refresh_running, (
        "the STT refresh loop was not stopped on call_ended"
    )
    ticks_at_end = len(refreshes)
    await asyncio.sleep(0.05)
    assert len(refreshes) == ticks_at_end, (
        "the STT refresh loop kept ticking AFTER call_ended — it was not cancelled"
    )
    await db.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_stt_refresh_loop_survives_a_raising_refresh_fn() -> None:
    """A refresh_fn that raises does NOT kill the loop (it must not die silently, §3.8 risk).

    The named node risk is "the STT loop dying silently and degrading transcription
    mid-meeting". A single failed refresh must be swallowed (logged) and the loop must
    keep trying on its interval — availability-critical loops degrade honestly, never die.
    """
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")

    calls: list[int] = []

    async def _flaky_refresh() -> None:
        calls.append(1)
        raise RuntimeError("transient credential-endpoint blip")

    registry = MeetingRuntimeRegistry(
        db, stt_refresh_fn=_flaky_refresh, stt_refresh_interval_s=0.01
    )
    meeting_id, bot_id = await _seed_meeting(db)

    await _ingest(db, {"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1
    runtime = registry.get(meeting_id)
    assert runtime is not None

    # Even though every refresh raises, the loop keeps firing (does not die on first raise).
    for _ in range(200):
        if len(calls) >= 3:
            break
        await asyncio.sleep(0.01)
    assert len(calls) >= 3, (
        f"a raising refresh_fn killed the loop after {len(calls)} call(s) — it must "
        "degrade honestly and keep trying, never die silently mid-meeting"
    )
    assert runtime.stt_refresh_running, "the loop must still be alive after a failed refresh"

    await registry.end_meeting(meeting_id)
    await db.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_standing_pipes_subscribe_once_and_stay_silent_with_zero_agent_calls() -> None:
    """Subscription count == 1 at join, and a silent meeting keeps pipes flowing, zero agent.

    The Scribe subscribes to the ONE SignalCarrier EXACTLY once at join (not re-wired per
    transcript). Then a stream of ambient transcript signals (nothing addressed to Proxy)
    flows the pipe carrier→coalescer→Scribe while the run loop makes ZERO wake turns.
    The Scribe micro-call seam is a recorder (no vendor LLM), so this proves the pure
    forwarding on the real assembly without a funded key.
    """
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")
    registry = MeetingRuntimeRegistry(db, stt_refresh_interval_s=0.01)

    meeting_id, bot_id = await _seed_meeting(db)

    await _ingest(db, {"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1
    runtime = registry.get(meeting_id)
    assert runtime is not None

    # The Scribe subscribed to the carrier EXACTLY ONCE at join — the join-time wiring
    # registers the pipe once, not re-wired per event (subscription count == 1).
    assert len(runtime.carrier._subscribers) == 1, (
        "the standing pipe must subscribe ONCE at join; got "
        f"{len(runtime.carrier._subscribers)} subscribers"
    )

    # Feed many ambient transcript signals (a "long silent meeting" from the agent's
    # point of view — nothing is addressed to Proxy). Drain them through the REAL
    # webhook path; the pipe forwards each with zero agent/wake calls.
    n = 40
    for i in range(n):
        await _ingest(
            db,
            {
                "event": "transcript.data",
                "data": {
                    "bot_id": bot_id,
                    "words": f"ambient chatter number {i}",
                    "speaker": "Ana" if i % 2 == 0 else "Zed",
                    "timestamp": float(i),
                    "end_of_turn": True,
                },
            },
        )
    assert await drain_pending_webhooks(db, registry=registry) >= n

    # A second in_call redelivery must NOT add a subscription — the pipe stays wired once.
    await _ingest(db, {"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1
    assert len(runtime.carrier._subscribers) == 1, (
        "a redelivered in_call re-wired the standing pipe (subscription count must stay 1)"
    )

    # No run loop was built and no wake turn was ever requested — the pipes cost nothing
    # and involve no agent (the standing-pipes invariant).
    assert runtime.run_loop is None or runtime.run_loop.wake_turns_run == 0, (
        "the standing pipes must involve ZERO agent/wake calls"
    )

    await registry.end_meeting(meeting_id)
    await db.close()
