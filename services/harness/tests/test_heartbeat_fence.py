"""D-003 acceptance — the operation_runs fence is the SOLE liveness authority.

Node: ``orchestrator.heartbeat-fence`` (fix). Decision D-003: the
``operation_runs`` fencing heartbeat in ``libs/ops/operation_run.py`` (§3.7 /
CANONICAL §12.10) is the CANONICAL liveness authority; the
``services/harness/heartbeat.py`` Healthchecks.io dead-man ping is AUXILIARY
observability, never the fence. Two liveness stories is a split-brain risk, so
this bundle proves one story:

  * ``heartbeat_fence`` — the real ``OperationHandle.heartbeat`` UPDATE gated on
    ``status='running'`` returns True while the row is running (rowcount 1) and,
    on a reclaimed/reaped row (rowcount 0), drives ``is_owner`` False — the
    self-terminate signal — AND the real gated emit frontier then refuses every
    outward verb (a fenced-out zombie reaches the wire zero times).
  * ``recovery`` — a re-claimed harness re-joins via Recall (restart-not-resume);
    it never resumes the dead media session and never checkpoint-resumes.
  * ``stale_reaper`` — the boot bulk sweep flips a stale running row to
    'interrupted' (crash detection is a staleness read, not a broker ack).
  * the Healthchecks ping is STATICALLY demoted: ``heartbeat.py`` names itself
    auxiliary observability and points at the operation_runs fence as the
    authority — so no reader can mistake the ping for the liveness fence.

Product imports live inside the test bodies so this module COLLECTS even before
the product exists (red-first). The DB-backed tests self-skip when no local
Postgres DSN is exported (the ``build/setup-test-env.sh`` harness provides one).
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_HEARTBEAT_SRC = _ROOT / "services" / "harness" / "src" / "harness" / "heartbeat.py"
_OPRUN_SRC = _ROOT / "libs" / "ops" / "src" / "ops" / "operation_run.py"
_EMIT_SRC = _ROOT / "services" / "harness" / "src" / "harness" / "emit.py"


def _dsn() -> str | None:
    for key in ("TEST_DATABASE_URL", "DATABASE_URL"):
        v = os.environ.get(key)
        if v and v.startswith("postgresql://"):
            return v
    return None


# ── heartbeat_fence: the operation_runs fence is the liveness authority ─────
@pytest.mark.integration
def test_heartbeat_fence_rowcount_zero_drives_is_owner_false() -> None:
    """The real fencing heartbeat: running→True (rowcount 1); reclaimed→is_owner False (rowcount 0)."""
    dsn = _dsn()
    if dsn is None:
        pytest.skip("no local Postgres DSN (run under build/setup-test-env.sh)")

    from libs.db import Database
    from libs.ops import with_operation_run

    async def _run() -> None:
        db = await Database.connect(dsn)
        try:
            async with db.acquire() as conn:
                await conn.execute("DELETE FROM operation_runs")
            # A live, owned run: the fencing heartbeat returns True (rowcount 1).
            async with with_operation_run(
                db, "fence-scope", "meeting-harness", heartbeat_s=3600
            ) as handle:
                assert await handle.heartbeat() is True, (
                    "owner's fencing heartbeat on a running row must keep is_owner True"
                )
                assert handle.is_owner is True

                # Reclaim the row out from under the owner (reaper flips it off
                # 'running'): the very next fencing heartbeat sees rowcount 0.
                async with db.acquire() as conn:
                    await conn.execute(
                        "UPDATE operation_runs SET status='interrupted', "
                        "completed_at=now() WHERE id=$1",
                        handle.run_id,
                    )
                assert await handle.heartbeat() is False, (
                    "a reclaimed row's fencing heartbeat must return rowcount 0 → is_owner False"
                )
                assert handle.is_owner is False, (
                    "rowcount 0 must drive is_owner False — the self-terminate signal"
                )
        finally:
            await db.close()

    asyncio.run(_run())


def test_heartbeat_fence_fenced_out_zombie_emits_nothing() -> None:
    """A handle whose fence dropped (is_owner False) gates EVERY side-effect emit to silence."""
    from harness.emit import EMIT_FRONTIER, Emitter

    class _FencedHandle:
        """Stand-in for an OperationHandle that just lost the fence."""

        is_owner = False

    emitted: list[tuple[str, object]] = []
    emitter = Emitter(handle=_FencedHandle(), sink=lambda v, p: emitted.append((v, p)))

    assert emitter.is_owner is False, "the emitter must read is_owner live off the handle"

    refused = 0
    for verb in sorted(EMIT_FRONTIER):
        fn = getattr(emitter, verb, None)
        assert callable(fn), f"emit frontier verb {verb!r} must be a gated method/attr"
        assert fn("payload") is False, (
            f"{verb} must refuse (return falsy) when the fence is lost"
        )
        refused += 1
    assert refused == len(EMIT_FRONTIER), "every frontier verb must gate on is_owner"
    assert emitted == [], (
        f"a fenced-out zombie must reach the wire zero times; leaked {len(emitted)}"
    )


def test_heartbeat_fence_live_handle_regains_and_loses_ownership() -> None:
    """The emitter reads is_owner LIVE: flipping the handle mid-turn instantly (un)gates the wire."""
    from harness.emit import Emitter

    class _Handle:
        is_owner = True

    handle = _Handle()
    wire: list[tuple[str, object]] = []
    emitter = Emitter(handle=handle, sink=lambda v, p: wire.append((v, p)))

    assert emitter.speak("hi") is True, "an owner may speak"
    handle.is_owner = False  # fence lost mid-turn
    assert emitter.speak("still here?") is False, (
        "a mid-turn fence loss must immediately silence the wire (live is_owner read)"
    )
    assert wire == [("speak", "hi")], "only the pre-fence-loss emission may reach the wire"


# ── stale_reaper: crash detection is a staleness read, not a broker ack ─────
@pytest.mark.integration
def test_stale_reaper_boot_sweep_flips_stale_running_to_interrupted() -> None:
    """The boot bulk sweep flips a stale 'running' row to 'interrupted' before routers mount."""
    dsn = _dsn()
    if dsn is None:
        pytest.skip("no local Postgres DSN (run under build/setup-test-env.sh)")

    from libs.db import Database

    async def _run() -> None:
        db = await Database.connect(dsn)
        try:
            async with db.acquire() as conn:
                await conn.execute("DELETE FROM operation_runs")
                # A killed instance's row: 'running' but heartbeat long stale.
                run_id = await conn.fetchval(
                    "INSERT INTO operation_runs "
                    "(scope_id, operation_type, status, last_heartbeat_at) "
                    "VALUES ('dead', 'meeting-harness', 'running', "
                    "now() - interval '10 minutes') RETURNING id"
                )
            swept = await db.sweep_stale_operation_runs()
            assert swept >= 1, "the boot sweep must flip the stale running row"
            async with db.acquire() as conn:
                status = await conn.fetchval(
                    "SELECT status FROM operation_runs WHERE id=$1", run_id
                )
            assert status == "interrupted", (
                f"a stale running row must become 'interrupted'; got {status!r}"
            )
            # Idempotent: a second sweep over the healed state is a no-op.
            assert await db.sweep_stale_operation_runs() == 0
        finally:
            await db.close()

    asyncio.run(_run())


# ── recovery: restart-not-resume (never resume the dead media session) ──────
@pytest.mark.integration
def test_recovery_replans_rejoin_never_resumes_media_session() -> None:
    """A re-claimed harness re-joins via Recall; it never resumes the dead media session."""
    dsn = _dsn()
    if dsn is None:
        pytest.skip("no local Postgres DSN (run under build/setup-test-env.sh)")

    from libs.db import Database
    from harness.recovery import recover_meeting_harness

    async def _run() -> None:
        db = await Database.connect(dsn)
        try:
            async with db.acquire() as conn:
                await conn.execute("DELETE FROM operation_runs")
                await conn.execute(
                    "INSERT INTO operation_runs "
                    "(scope_id, operation_type, status, progress) "
                    "VALUES ('m-rec', 'meeting-harness', 'interrupted', "
                    "'{\"transcript_offset\": 42}')"
                )
            plan = await recover_meeting_harness(db, "m-rec")
            assert plan.rejoin_recall is True, "recovery must re-join via Recall"
            assert plan.resume_media_session is False, (
                "recovery must NEVER resume the dead media session (restart-not-resume)"
            )
            assert plan.checkpoint_resume is False, "no fine-grained checkpoint resume"
            assert plan.replay_from == 42, "recovery replays from the persisted progress offset"
        finally:
            await db.close()

    asyncio.run(_run())


# ── the Healthchecks ping is STATICALLY demoted to auxiliary (D-003) ────────
def test_heartbeat_fence_healthchecks_ping_is_auxiliary_not_the_fence() -> None:
    """heartbeat.py must NAME itself auxiliary observability and defer to the operation_runs fence.

    D-003 forbids the split-brain framing where the Healthchecks dead-man ping is
    presented as *the* liveness authority. The product source must (a) declare the
    ping AUXILIARY / observability-only, (b) explicitly disclaim being the fence,
    and (c) point at the operation_runs fencing heartbeat as the authority — so no
    reader (or future edit) mistakes the ping for the liveness fence.
    """
    src = _HEARTBEAT_SRC.read_text()
    low = src.lower()

    assert "auxiliary" in low, (
        "heartbeat.py must declare the Healthchecks ping AUXILIARY (D-003 demotion)"
    )
    assert "operation_runs" in low, (
        "heartbeat.py must point at the operation_runs fence as the liveness authority"
    )
    # It must explicitly disclaim being the fence / the liveness authority.
    assert re.search(
        r"not\s+the\s+(fence|liveness|authorit)", low
    ), "heartbeat.py must explicitly state the ping is NOT the fence / liveness authority"
    # And it must NOT still frame itself as THE harness-liveness authority.
    assert "harness liveness — a healthchecks" not in low, (
        "heartbeat.py's headline must not present the Healthchecks ping as harness liveness"
    )


def test_heartbeat_fence_operation_runs_is_the_named_authority() -> None:
    """The canonical fence source names itself the fence + maps rowcount-0 → is_owner False."""
    op = _OPRUN_SRC.read_text().lower()
    assert "fenc" in op, "operation_run.py must describe itself as the fence"
    assert "is_owner" in op and "rowcount" in op or "affected" in op, (
        "the fence must map a zero-rowcount heartbeat to is_owner False"
    )
    emit = _EMIT_SRC.read_text()
    assert "is_owner" in emit and "EMIT_FRONTIER" in emit, (
        "the emit frontier must be enumerated and gated on the fence's is_owner"
    )
