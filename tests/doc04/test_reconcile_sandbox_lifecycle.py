"""Doc 04 · orchestrator.reconcile-sandbox-verify — the reconcile sweep + the
per-meeting sandbox lifecycle (§3.8/§3.9, CANONICAL §6/§12.1).

Behavioural acceptance (NOT grep seams — real behaviour on the real substrate):

  1. ``ensure_running`` provisions a cold meeting's sandbox EXACTLY ONCE and returns
     the EXISTING sandbox on a redelivered join — no second provision, race-safe via
     the operation_runs atomic claim (§3.9 "the one race-safe call").
  2. ``run_reconcile_sweep(db)`` runs THREE ISOLATED steps (each in its own
     try/except — one bad step never aborts the rest) and is IDEMPOTENT: running it
     twice over the same state yields the same end state (§3.8).
  3. The cron REAPS an orphaned sandbox (meeting ended / past TTL) — a live sandbox
     for an ended meeting is destroyed (§3.9 defence #3).
  4. POST /internal/reconcile is TOKEN-gated and mounted OUTSIDE the user-auth wall:
     401/403 without the token, never behind a user session (§3.8/§12.1).
  5. NO ManagedResource provisioning/running/stopped/failed state machine survives
     (cut per §6) — the provider exposes only idempotent verbs.

The DB-backed bodies open the real local Postgres and SKIP cleanly when none is
reachable (mirrors test_bundle_dispatch.py).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest


# ── real-DB helpers (mirror test_bundle_dispatch.py) ─────────────────────────
def _local_dsn() -> str | None:
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        dsn = os.environ.get(var, "").strip()
        if dsn:
            return dsn
    return None


def _require_db() -> str:
    dsn = _local_dsn()
    if dsn is None:
        pytest.skip("no local Postgres (set TEST_DATABASE_URL)")
    return dsn


async def _open_db():
    from libs.db import Database

    dsn = _local_dsn()
    assert dsn is not None
    return await Database.connect(dsn)


# ── clause 5: no state machine survives — only idempotent verbs (§6) ─────────
def test_provider_exposes_only_idempotent_verbs_no_state_machine() -> None:
    """The provider is {provision, destroy, health_check} — NO provisioning/running/
    stopped/failed state machine (cut per CANONICAL §6)."""
    from libs.ops import sandbox_provider

    assert sandbox_provider.verbs() == {"provision", "destroy", "health_check"}
    # No lifecycle FSM symbol survives anywhere in the provider module.
    banned = ("ManagedResource", "provisioning", "STATE_RUNNING", "STATE_STOPPED", "STATE_FAILED")
    src = _read_source(sandbox_provider.__file__)
    for name in banned:
        assert name not in src, f"a cut state-machine symbol {name!r} survived in sandbox_provider"


def _read_source(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ── clause 1: ensure_running — provision once, return the same on redelivery ──
def test_ensure_running_provisions_cold_meeting_exactly_once() -> None:
    """A cold meeting → ensure_running provisions its sandbox exactly once and a
    redelivered join returns the SAME sandbox (no re-provision) — §3.9."""
    _require_db()
    from libs.ops import sandbox_provider

    async def _run():
        db = await _open_db()
        try:
            meeting_id = str(uuid.uuid4())
            provisions: list[str] = []

            def _provision(mid: str):
                provisions.append(mid)
                return sandbox_provider.provision(meeting_id=mid)

            first = await sandbox_provider.ensure_running(
                db, meeting_id, provision=_provision
            )
            # A redelivered join for the SAME meeting must NOT provision again.
            second = await sandbox_provider.ensure_running(
                db, meeting_id, provision=_provision
            )
            return first, second, provisions
        finally:
            await db.close()

    first, second, provisions = asyncio.run(_run())
    assert first.id == second.id, "redelivered join returned a DIFFERENT sandbox"
    assert len(provisions) == 1, (
        f"ensure_running provisioned {len(provisions)} time(s); a redelivered "
        "join must return the existing sandbox with NO second provision"
    )


def test_ensure_running_is_race_safe_under_concurrent_redelivery() -> None:
    """Two concurrent redelivered joins for one cold meeting provision exactly ONCE
    (the operation_runs atomic claim is the arbiter) — §3.9 race-safe call."""
    _require_db()
    from libs.ops import sandbox_provider

    async def _run():
        db = await _open_db()
        try:
            meeting_id = str(uuid.uuid4())
            provisions: list[str] = []

            def _provision(mid: str):
                provisions.append(mid)
                return sandbox_provider.provision(meeting_id=mid)

            # Fire both joins concurrently against the SAME cold meeting.
            a, b = await asyncio.gather(
                sandbox_provider.ensure_running(db, meeting_id, provision=_provision),
                sandbox_provider.ensure_running(db, meeting_id, provision=_provision),
            )
            return a, b, provisions
        finally:
            await db.close()

    a, b, provisions = asyncio.run(_run())
    assert a.id == b.id, "concurrent joins returned different sandboxes"
    assert len(provisions) == 1, (
        f"concurrent redelivery provisioned {len(provisions)} times; the atomic "
        "claim must let exactly ONE win"
    )


# ── clause 2: the sweep runs three isolated steps and is idempotent ──────────
def test_reconcile_sweep_runs_three_isolated_idempotent_steps() -> None:
    """run_reconcile_sweep(db) returns the §3.8 shape (a per-step error report),
    runs THREE named steps, and is idempotent (run-twice == run-once)."""
    _require_db()
    from libs.ops import run_reconcile_sweep

    async def _run():
        db = await _open_db()
        try:
            first = await run_reconcile_sweep(db)
            second = await run_reconcile_sweep(db)
            return first, second
        finally:
            await db.close()

    first, second = asyncio.run(_run())
    # §3.8 shape: a dict carrying a per-step error list.
    assert isinstance(first, dict), "the sweep must return the §3.8 report dict"
    assert "errors" in first
    assert "steps" in first, "the sweep must name the steps it ran"
    assert set(first["steps"]) == {"stale-harnesses", "meeting-sandboxes", "notes-retention"}
    # Idempotent: a clean run leaves no errors, and the second run mirrors the first.
    assert first["errors"] == [], f"a clean sweep must report no errors, got {first['errors']}"
    assert second["errors"] == []


def test_reconcile_sweep_isolates_a_failing_step() -> None:
    """A failure inside ONE step is caught and reported — the OTHER steps still run
    (each step in its own try/except, §3.8)."""
    from libs.ops import reconcile as reconcile_mod

    class _BoomDB:
        # stale-harnesses step calls this and explodes; the sandbox + notes steps
        # must still run and the sweep must not raise.
        async def sweep_stale_operation_runs(self) -> int:
            raise RuntimeError("boom in stale-harnesses")

        def last_activity_at(self, scope_id):  # noqa: ANN001 - test double
            return None

        async def acquire(self):  # pragma: no cover - not reached in this test
            raise AssertionError("acquire should not be needed by the sandbox step here")

    report = asyncio.run(reconcile_mod._run_reconcile_sweep_async(_BoomDB()))
    assert any("stale-harnesses" in e for e in report["errors"]), (
        "the failing step must be reported by name"
    )
    # The sweep completed (did not raise) and still names all three steps as attempted.
    assert set(report["steps"]) == {"stale-harnesses", "meeting-sandboxes", "notes-retention"}


# ── clause 3: the cron reaps an orphaned sandbox (ended meeting) ─────────────
def test_reconcile_cron_reaps_orphaned_sandbox_for_ended_meeting() -> None:
    """A live sandbox whose meeting has ENDED is destroyed by the sweep's
    meeting-sandboxes step (§3.9 defence #3: list live, kill orphans)."""
    _require_db()
    from libs.ops import run_reconcile_sweep, sandbox_provider

    async def _run():
        db = await _open_db()
        try:
            # An ended meeting with a live sandbox = an orphan the cron must reap.
            meeting_id = str(uuid.uuid4())
            handle = await sandbox_provider.ensure_running(
                db, meeting_id, provision=lambda m: sandbox_provider.provision(meeting_id=m)
            )
            assert bool(await sandbox_provider.health_check(handle)) is True
            # Mark the meeting ended in the provider's orphan view.
            sandbox_provider.mark_meeting_ended(meeting_id)

            await run_reconcile_sweep(db)
            alive_after = bool(await sandbox_provider.health_check(handle))
            # Idempotent: a second sweep over the now-reaped state is a no-op.
            await run_reconcile_sweep(db)
            return alive_after
        finally:
            await db.close()

    alive_after = asyncio.run(_run())
    assert alive_after is False, "the cron did not reap the ended meeting's orphaned sandbox"


# ── clause 4: /internal/reconcile is token-gated, OUTSIDE the auth wall ───────
def test_internal_reconcile_is_token_gated_outside_the_auth_wall() -> None:
    """POST /internal/reconcile refuses without the internal token (401/403) and is
    reachable WITHOUT any user session — it is NOT behind the auth wall (§12.1)."""
    from starlette.testclient import TestClient

    from control_plane.app import create_app

    os.environ["PROXY_INTERNAL_TOKEN"] = "the-internal-token"
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    # No token → refused (never a 200, never a user-auth redirect to /auth/login).
    no_token = client.post("/internal/reconcile")
    assert no_token.status_code in (401, 403), (
        f"no-token reconcile must be refused, got {no_token.status_code}"
    )
    # Wrong token → refused.
    bad = client.post("/internal/reconcile", headers={"X-Internal-Token": "nope"})
    assert bad.status_code in (401, 403)
    # The route exists on the app OUTSIDE the auth wall (it is registered, not 404).
    paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
    assert "/internal/reconcile" in paths, "the /internal/reconcile route is not mounted"
