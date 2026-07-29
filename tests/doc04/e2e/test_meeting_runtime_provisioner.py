"""e2e: the per-meeting runtime provisioner — the webhook→claim→assemble→run entry.

Node ``orchestrator.meeting-runtime-provisioner`` (04 §3.2/§3.6, CANONICAL §12.1).

The provisioner is the entry-point that turns the built pieces into a RUNNING meeting.
These tests drive the REAL production path against the live test Postgres — a Recall
``in_call`` webhook body handed to ``provision_meeting`` — and assert the DoD:

  * **atomic claim** — the provisioner claims the meeting via ``ops.claim_meeting``
    (the ``operation_runs`` partial-unique index); a 2nd CONCURRENT claim of the same
    meeting LOSES (one harness per meeting, §3.6). The winner opens the harness; the
    loser backs off and never assembles a second runtime;
  * **single-scope assembly + subscribe-once** — the winner instantiates all four
    subsystems (transport carrier / scribe runtime / run loop / abort) in ONE scope and
    subscribes the ``SignalCarrier`` EXACTLY ONCE at join (not re-wired per event);
  * **the loop is launched from the webhook** — the provisioner starts the run-loop
    event queue (``asyncio.run``/task) so signals route THROUGH the loop; it is not a
    dead standalone module;
  * **meeting_end tears down** — the loop runs until the ``meeting_end`` webhook (or a
    timeout), then the runtime is torn down and the operation row completes;
  * **fencing** — every emit is gated on ``is_owner`` off the claimed row's handle;
  * **recycle re-claim + resume** — after the owner's row goes stale (its instance
    died), a REPLACEMENT provisioner re-claims the same meeting and resumes it via
    ``ops.resume_with_fallback`` (the pinned §3.5 history-replay seam).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

# Import Database from the SAME path the ``ops`` claim/cost seams dispatch on
# (``libs.db``): ``claim_meeting`` uses ``isinstance(db, libs.db.Database)`` to pick the
# async persisted claim, and the ``db``-package alias is a DIFFERENT class object.
from libs.db import Database, open_pool, repos

from control_plane.meeting_runtime import MeetingRuntimeRegistry
from control_plane.provisioner import (
    ProvisionOutcome,
    make_provision_launcher,
    provision_meeting,
    run_meeting_until_end,
)
from control_plane.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned this session"
)

MEETING_HARNESS_OP = "meeting-harness"


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


def _in_call(bot_id: str) -> dict:
    return {"event": "bot.in_call", "data": {"bot_id": bot_id}}


async def _ingest(db: Database, payload: dict) -> None:
    guid = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(conn, guid, payload)


async def _running_row(db: Database, meeting_id: str) -> dict | None:
    async with db.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, status, created_by FROM operation_runs "
            "WHERE scope_id = $1 AND operation_type = $2 AND status = 'running'",
            meeting_id,
            MEETING_HARNESS_OP,
        )


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_in_call_webhook_atomically_claims_and_assembles_once() -> None:
    """An in_call webhook → atomic claim → single-scope assembly, carrier subscribed once."""
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    registry = MeetingRuntimeRegistry(db)
    meeting_id, bot_id = await _seed_meeting(db)

    outcome = await provision_meeting(_in_call(bot_id), db=db, registry=registry)

    # The claim WON — the provisioner owns the meeting.
    assert outcome.claimed is True, "the in_call webhook did not claim the meeting"
    assert outcome.run_id is not None, "a won claim must carry the operation_runs id"
    row = await _running_row(db, meeting_id)
    assert row is not None, "the atomic claim did not leave a running operation_runs row"
    assert str(row["id"]) == str(outcome.run_id)

    # The four subsystems assembled in ONE scope, on ONE runtime.
    runtime = registry.get(meeting_id)
    assert runtime is not None, "the provisioner did not assemble a MeetingRuntime"
    assert runtime.run_loop is not None, "the run loop was not assembled at provision"
    # THE CUTOVER: the NEW in-meeting engine is the brain on the boot path — assembled
    # at provision, reachable by meeting id off the registry entry, with its speak pipe
    # + warm sandbox handle stashed for the meeting-end lifecycle. The OLD live brain is
    # no longer wired here.
    assert runtime.engine is not None, "the in-meeting engine was not assembled at provision"
    assert runtime.speak_pipe is not None, "the speak pipe was not stashed at provision"
    assert runtime.engine_sandbox is not None, "the warm sandbox handle was not stashed"
    assert runtime.live_brain is None, "the OLD live brain must no longer own the boot path"
    # The operation handle is bound so is_owner fencing gates every emit.
    assert runtime.operation_handle is not None, "the claimed row's handle was not bound"
    assert runtime.operation_handle.is_owner is True

    # The SignalCarrier was subscribed EXACTLY ONCE at join (scribe + orchestrator pipe
    # share the one carrier; the provisioner must not re-wire per event). Count the live
    # subscribers registered on the carrier after assembly.
    subs = len(runtime.carrier._subscribers)  # scribe consumer + orchestrator pipe
    assert subs >= 1, "no subscriber was wired onto the carrier at join"

    # Wire the orchestrator pipe once and confirm re-provision does NOT add a second.
    before = len(runtime.carrier._subscribers)
    again = await provision_meeting(_in_call(bot_id), db=db, registry=registry)
    assert again.claimed is False, "a redelivered in_call must NOT open a 2nd harness"
    assert len(runtime.carrier._subscribers) == before, (
        "a redelivered in_call re-wired the carrier (subscribe must be ONCE at join)"
    )

    await registry.end_meeting(meeting_id)
    await pool.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_concurrent_claims_one_wins_one_loses() -> None:
    """Two provisioners handed the SAME in_call race the claim; exactly one wins (§3.6)."""
    pool = await open_pool(_DSN)
    db_a = Database(pool, f"inst-A-{uuid.uuid4().hex[:6]}")
    db_b = Database(pool, f"inst-B-{uuid.uuid4().hex[:6]}")
    reg_a = MeetingRuntimeRegistry(db_a)
    reg_b = MeetingRuntimeRegistry(db_b)
    meeting_id, bot_id = await _seed_meeting(db_a)

    # Both instances get the at-least-once webhook at the same time.
    out_a, out_b = await asyncio.gather(
        provision_meeting(_in_call(bot_id), db=db_a, registry=reg_a),
        provision_meeting(_in_call(bot_id), db=db_b, registry=reg_b),
    )

    winners = [o for o in (out_a, out_b) if o.claimed]
    losers = [o for o in (out_a, out_b) if not o.claimed]
    assert len(winners) == 1, f"exactly one harness must win the claim; got {len(winners)}"
    assert len(losers) == 1, "the concurrent duplicate must lose and back off"

    # Exactly ONE running row exists for the meeting (one harness per meeting).
    async with db_a.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM operation_runs "
            "WHERE scope_id = $1 AND operation_type = $2 AND status = 'running'",
            meeting_id,
            MEETING_HARNESS_OP,
        )
    assert int(cnt) == 1, f"one harness per meeting; found {cnt} running rows"

    # The loser assembled NO runtime (it backed off, never opened a second harness).
    if out_a.claimed:
        assert reg_b.get(meeting_id) is None, "the loser opened a harness anyway"
        await reg_a.end_meeting(meeting_id)
    else:
        assert reg_a.get(meeting_id) is None, "the loser opened a harness anyway"
        await reg_b.end_meeting(meeting_id)
    await pool.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_loop_is_launched_from_webhook_and_meeting_end_tears_down() -> None:
    """The provisioner LAUNCHES the run loop (signals route through it); meeting_end ends it.

    ``run_meeting_until_end`` is the ``asyncio.run``-style entry the provisioner launches:
    it assembles, subscribes the carrier once, runs the loop as a task, and returns when
    the meeting_end signal (or timeout) closes the carrier — proving the loop was actually
    launched from the webhook, not left as a dead module.
    """
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    registry = MeetingRuntimeRegistry(db)
    meeting_id, bot_id = await _seed_meeting(db)

    # Launch the meeting; it runs until we signal end. Ambient signals fed onto the
    # carrier must ROUTE THROUGH the loop (events_routed climbs) with zero wake turns.
    async def _drive() -> ProvisionOutcome:
        return await run_meeting_until_end(
            _in_call(bot_id), db=db, registry=registry, timeout_s=5.0
        )

    task = asyncio.create_task(_drive())

    # Wait for the runtime to come up.
    for _ in range(200):
        rt = registry.get(meeting_id)
        if rt is not None and rt.run_loop is not None:
            break
        await asyncio.sleep(0.01)
    runtime = registry.get(meeting_id)
    assert runtime is not None and runtime.run_loop is not None, "loop was not launched"

    # Emit an ambient signal onto the ONE carrier; it must flow through the loop.
    from transport.signals import Boundary

    await runtime.carrier.emit(Boundary(t=0.0))
    for _ in range(200):
        if runtime.run_loop.events_routed >= 1:
            break
        await asyncio.sleep(0.01)
    assert runtime.run_loop.events_routed >= 1, (
        "the emitted signal never routed through the loop — the loop was not launched"
    )
    assert runtime.run_loop.wake_turns_run == 0, "an ambient signal must not wake Proxy"

    # meeting_end tears the loop down and the entry returns.
    from transport.signals import MeetingEnd

    await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
    outcome = await asyncio.wait_for(task, timeout=6.0)
    assert outcome.ran_to_end is True, "the loop did not run to meeting end"

    # The meeting is no longer running (the operation row completed on teardown).
    assert registry.get(meeting_id) is None, "the runtime was not dropped at meeting end"
    await pool.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_recycle_replacement_reclaims_and_resumes() -> None:
    """After the owner's row goes stale (its instance died), a replacement RE-CLAIMS + resumes.

    The recycle survival arc (J-09-S5): the first provisioner claims; its instance is
    then "killed" (we flip its running row to interrupted, as the boot-reaper does when
    the heartbeat goes stale). A REPLACEMENT provisioner handed the same in_call must
    re-claim the freed meeting and resume it via ``ops.resume_with_fallback``.
    """
    pool = await open_pool(_DSN)
    db1 = Database(pool, f"inst-1-{uuid.uuid4().hex[:6]}")
    db2 = Database(pool, f"inst-2-{uuid.uuid4().hex[:6]}")
    reg1 = MeetingRuntimeRegistry(db1)
    reg2 = MeetingRuntimeRegistry(db2)
    meeting_id, bot_id = await _seed_meeting(db1)

    first = await provision_meeting(_in_call(bot_id), db=db1, registry=reg1)
    assert first.claimed is True

    # A second provisioner on a LIVE row loses (one harness per meeting).
    blocked = await provision_meeting(_in_call(bot_id), db=db2, registry=reg2)
    assert blocked.claimed is False, "a replacement must not steal a LIVE meeting"

    # The owning instance dies: the reaper flips its stale running row to interrupted
    # (exactly what control_plane.server.reap_orphans / sweep_stale_operation_runs does).
    async with db1.acquire() as conn:
        await conn.execute(
            "UPDATE operation_runs SET status = 'interrupted', completed_at = now() "
            "WHERE scope_id = $1 AND operation_type = $2 AND status = 'running'",
            meeting_id,
            MEETING_HARNESS_OP,
        )

    # The replacement re-claims the now-freed meeting and resumes it.
    resumed = await provision_meeting(
        _in_call(bot_id), db=db2, registry=reg2, resume=True
    )
    assert resumed.claimed is True, "the replacement did not re-claim the freed meeting"
    assert resumed.resumed is True, (
        "the replacement re-claim did not resume via ops.resume_with_fallback (§3.5)"
    )
    row = await _running_row(db2, meeting_id)
    assert row is not None, "the re-claim left no running row"
    assert str(row["created_by"]) == db2.instance_id, (
        "the replacement is not the new owner (created_by affinity, §3.6/§11.11)"
    )

    await reg2.end_meeting(meeting_id)
    await pool.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_provisioner_reachable_on_the_real_webhook_drain_path() -> None:
    """The provisioner is LIVE-reachable through the real webhook drain, not a dead module.

    Drives the actual production seam: a Recall ``in_call`` webhook row is durably ingested,
    then ``drain_pending_webhooks(db, registry, launch=make_provision_launcher(...))`` — the
    meeting_runtime deployable's drain — routes it THROUGH the provisioner. The provisioner
    atomically claims + launches the loop as a background task; a running operation_runs row
    proves the claim happened on the real path. meeting_end then tears it down.
    """
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    registry = MeetingRuntimeRegistry(db)
    meeting_id, bot_id = await _seed_meeting(db)

    live_tasks: set[asyncio.Task] = set()
    launch = make_provision_launcher(db, registry, timeout_s=5.0, tasks=live_tasks)

    # Durably record the in_call callback, then drain it through the REAL production path.
    await _ingest(db, _in_call(bot_id))
    drained = await drain_pending_webhooks(db, registry=registry, launch=launch)
    assert drained >= 1, "the in_call webhook row was not drained"

    # The loop was launched as a BACKGROUND task (the drain returns 200 while the meeting
    # runs for hours). The claim + assembly happen on that task, so poll for them.
    assert len(live_tasks) == 1, "the meeting loop was not launched as a background task"
    for _ in range(300):
        rt = registry.get(meeting_id)
        if rt is not None and rt.run_loop is not None:
            break
        await asyncio.sleep(0.01)
    runtime = registry.get(meeting_id)
    assert runtime is not None and runtime.run_loop is not None, (
        "the provisioner did not assemble + launch the runtime on the drain path"
    )
    # The claim happened on the real drain path: a running operation_runs row exists.
    row = await _running_row(db, meeting_id)
    assert row is not None, "the drain path did not atomically claim the meeting"

    # meeting_end ends the launched loop; the background task completes and drops.
    from transport.signals import MeetingEnd

    await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
    (task,) = tuple(live_tasks)
    outcome = await asyncio.wait_for(task, timeout=6.0)
    assert outcome.claimed is True and outcome.ran_to_end is True
    assert registry.get(meeting_id) is None, "the runtime was not dropped at meeting end"
    await pool.close()
