"""e2e: the provisioner seam is LIVE in the BOOTED meeting_runtime deployable.

Node ``orchestrator.meeting-runtime-provisioner`` — reachability on the real boot path
(04 §3.2/§3.6, CANONICAL §12.1).

The sibling e2e (``test_meeting_runtime_provisioner``) proves the provisioner works when
called directly. This test closes the REACHABILITY gap the fresh verifier flagged: that
no BOOTED deployable wired the provisioner into a webhook drain, so the in_call→running-
meeting chain was a test-only island. Here we drive the ACTUAL production boot step
``harness.server._real_provisioner_ready`` (the step the FastAPI lifespan runs) against
the live test Postgres and prove:

  * the boot step wires the provisioner launcher onto ``app.state.provision_launch`` and
    starts the periodic webhook-drain task (``app.state.webhook_drain_task``);
  * that booted drain loop — with no per-test launch injected — routes a durably-ingested
    Recall ``in_call`` THROUGH the provisioner: it atomically claims the meeting (a running
    ``operation_runs`` row appears) and launches + assembles the runtime;
  * ``_shutdown_real`` cancels the drain task cleanly.

So the seam is live on the real path, not a dead module only the direct e2e reaches.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from libs.db import Database, open_pool, repos

from harness import server as server_mod

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned this session"
)

MEETING_HARNESS_OP = "meeting-harness"


class _App:
    """Minimal FastAPI-app stand-in: the boot steps only touch ``.state``."""

    def __init__(self) -> None:
        self.state = type("_State", (), {})()


async def _seed_meeting(db: Database) -> tuple[str, str]:
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
async def test_boot_step_wires_provisioner_and_drain_routes_in_call() -> None:
    """The real boot step wires the launcher + drain; a drained in_call is claimed live."""
    pool = await open_pool(_DSN)
    db = Database(pool, f"boot-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    # Faster drain window so the test does not wait the full production interval.
    prev_interval = server_mod.WEBHOOK_DRAIN_INTERVAL_S
    server_mod.WEBHOOK_DRAIN_INTERVAL_S = 0.02

    app = _App()
    app.state.db = db
    meeting_id, bot_id = await _seed_meeting(db)

    # Durably land the Recall in_call BEFORE boot completes (the classic raced-boot case
    # the boot-time drain must catch), via the SAME durable surface the ingest path uses.
    guid = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(
            conn, guid, {"event": "bot.in_call", "data": {"bot_id": bot_id}}
        )

    try:
        # Drive the ACTUAL production boot step (the lifespan's provisioner_ready step).
        await server_mod._real_provisioner_ready(app)

        # The boot step wired the provisioner seam, not the Scribe-only start.
        assert callable(app.state.provision_launch), (
            "the boot step did not wire the provisioner launcher (reachability gap)"
        )
        assert app.state.webhook_drain_task is not None, (
            "the boot step did not start the periodic webhook-drain task"
        )
        assert app.state.meeting_runtimes is not None

        # The BOOTED drain loop routes the in_call THROUGH the provisioner: poll for the
        # atomic claim (a running operation_runs row) to appear on the real path.
        registry = app.state.meeting_runtimes
        for _ in range(300):
            if await _running_row(db, meeting_id) is not None:
                break
            await asyncio.sleep(0.01)
        row = await _running_row(db, meeting_id)
        assert row is not None, (
            "the booted drain loop did not claim the in_call through the provisioner — "
            "the seam is not live on the real path"
        )

        # The runtime was assembled + launched by the provisioner (not a Scribe-only start).
        for _ in range(300):
            rt = registry.get(meeting_id)
            if rt is not None and rt.run_loop is not None and rt.engine is not None:
                break
            await asyncio.sleep(0.01)
        runtime = registry.get(meeting_id)
        assert runtime is not None and runtime.run_loop is not None, (
            "the provisioner did not assemble + launch the runtime on the booted drain path"
        )
        # THE CUTOVER: the booted drain path assembles the NEW in-meeting engine (the
        # brain seat) — reachable by meeting id off the registry entry, old brain absent.
        assert runtime.engine is not None, (
            "the booted drain path did not assemble the in-meeting engine (cutover seam dead)"
        )
        assert runtime.live_brain is None, "the OLD live brain must no longer own the boot path"
        # is_owner fencing is bound off the claimed row's handle.
        assert runtime.operation_handle is not None
        assert runtime.operation_handle.is_owner is True

        # meeting_end tears the launched loop down; the meeting task drops.
        from transport.signals import MeetingEnd

        await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
        for _ in range(600):
            if registry.get(meeting_id) is None:
                break
            await asyncio.sleep(0.01)
        assert registry.get(meeting_id) is None, "the runtime was not dropped at meeting end"

        # _shutdown_real cancels the drain task cleanly.
        await server_mod._shutdown_real(app)
        assert app.state.webhook_drain_task.cancelled() or (
            app.state.webhook_drain_task.done()
        ), "the drain task was not cancelled on shutdown"
    finally:
        server_mod.WEBHOOK_DRAIN_INTERVAL_S = prev_interval
        # _shutdown_real closed the pool via db.close(); guard a double-close.
        with __import__("contextlib").suppress(Exception):
            await pool.close()
