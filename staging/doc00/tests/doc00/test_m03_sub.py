"""Doc 00 · §5 The durable-ops substrate — broker-free (AC-SUB-001..038).

Milestone m03. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports live INSIDE the test bodies, so this module COLLECTS
clean and FAILS red before ``libs/ops`` / ``libs/db`` / ``services/*`` exist.

Oracle sources per PROTO-DETERMINISTIC-01:
  * [contract]  — schema introspection over the migrated Postgres (canonical
                  column sets, FK edges, uuid types).
  * [model-stateful] — real SQL / product repo behaviour on a live connection
                  (partial unique index, ON CONFLICT dedupe, fencing rowcount,
                  transactional coupling).
  * [integration] — the spec-derived product runtime interfaces
                  (with_operation_run, atomic claim, reconcile sweep, sandbox
                  provider, cost reload, staged drafts, session/binding flow).
  * [static]    — call/def/import scans over the product tree (no in-memory
                  cross-process lock, no broker, no ManagedResource FSM, no warm
                  pool, no meeting_events bus, emit-frontier completeness).
  * [security-adversarial] — a reclaimed zombie must emit nothing.

Postgres-backed bodies import the product FIRST (so missing-product = red),
then open ``S.pg_conn()`` which SKIPS when no local database is present.
"""

import re

import pytest

import _support as S


# Canonical operation_runs column set (spec §5.1 DDL, CANONICAL §2).
_OPRUN_COLS = {
    "id", "scope_id", "operation_type", "status", "progress", "result_ref",
    "error", "pause_requested", "created_by", "started_at", "completed_at",
    "last_heartbeat_at",
}
# Canonical meeting_cost persisted column set (spec §5.6, CANONICAL §3).
_MEETING_COST_COLS = {
    "meeting_id", "model_usd", "cache_read_usd", "cache_creation_usd",
    "transport_usd", "e2b_usd", "started_at", "updated_at",
}
# Canonical staged_drafts column set (spec §5.6, CANONICAL §4).
_STAGED_DRAFT_COLS = {
    "draft_id", "meeting_id", "kind", "summary", "artifact_ref", "status",
    "created_at",
}
# The operation_runs.status value domain (spec §5.1 comment).
_OPRUN_STATUS_DOMAIN = {"running", "completed", "failed", "interrupted"}
# The complete emit / delivery / side-effect frontier (spec §5.1 + §12.3).
_EMIT_FRONTIER = {"speak", "send_chat", "show_screen", "apply", "dispatch"}


def _fk_targets(conn, table: str, column: str) -> set[tuple[str, str]]:
    """{(referenced_table, referenced_column)} for FKs on ``table.column``."""
    cur = conn.execute(
        """
        SELECT ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_name = %s AND kcu.column_name = %s
        """,
        (table, column),
    )
    return {(r[0], r[1]) for r in cur.fetchall()}


# ── AC-SUB-001 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_sub_001_operation_runs_canonical_columns_no_harness_table():
    """AC-SUB-001: one operation_runs table with the canonical column set; no meeting_harness table."""
    import libs.db  # red before product
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

        cols = S.table_columns(conn, "operation_runs")
        assert set(cols) == _OPRUN_COLS, (
            f"operation_runs columns != canonical: "
            f"extra={set(cols) - _OPRUN_COLS}, missing={_OPRUN_COLS - set(cols)}"
        )
        # id is uuid PK; scope_id/operation_type/status are NOT NULL text.
        assert cols["id"] == "uuid", f"id must be uuid; got {cols['id']!r}"
        for c in ("scope_id", "operation_type", "status"):
            assert cols[c] == "text", f"{c} must be text; got {cols[c]!r}"
        nn = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='operation_runs' AND is_nullable='NO'",
        )
        not_null = {row[0] for row in nn.fetchall()}
        assert {"scope_id", "operation_type", "status"} <= not_null, (
            f"scope_id/operation_type/status must be NOT NULL; got NOT NULL set {not_null}"
        )
        # No separate meeting_harness table.
        assert not S.table_exists(conn, "meeting_harness"), (
            "a separate meeting_harness table exists (Doc 04's table must be deleted)"
        )


# ── AC-SUB-002 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_002_partial_unique_index_one_running_per_scope():
    """AC-SUB-002: partial unique index allows exactly one running row per (scope_id, operation_type)."""
    import libs.db  # red before product
    import psycopg
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

        # The index predicate must be WHERE status='running'.
        idx = S.index_defs(conn, "operation_runs")
        active = [d for d in idx.values() if "unique" in d.lower()
                  and "scope_id" in d and "operation_type" in d]
        assert active, f"no partial unique index on (scope_id, operation_type): {idx}"
        assert any(re.search(r"where\s*\(?\s*status\s*=\s*'running'", d, re.I)
                   for d in active), (
            f"partial unique index predicate must be WHERE status='running': {active}"
        )

        conn.execute("DELETE FROM operation_runs")
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status) "
            "VALUES (%s, %s, 'running')", ("S", "T"),
        )
        # A second running insert for the same (S, T) must violate the index.
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO operation_runs (scope_id, operation_type, status) "
                "VALUES (%s, %s, 'running')", ("S", "T"),
            )


# ── AC-SUB-003 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_003_completed_row_does_not_block_reclaim():
    """AC-SUB-003: a completed/interrupted row does NOT block a new running row."""
    import libs.db  # red before product
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

        conn.execute("DELETE FROM operation_runs")
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status) "
            "VALUES (%s, %s, 'completed')", ("S", "T"),
        )
        # The new running insert must succeed — the partial index only
        # constrains status='running'; non-running rows never block re-claim.
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status) "
            "VALUES (%s, %s, 'running')", ("S", "T"),
        )
        cur = conn.execute(
            "SELECT count(*) FROM operation_runs "
            "WHERE scope_id=%s AND operation_type=%s AND status='running'",
            ("S", "T"),
        )
        assert cur.fetchone()[0] == 1, "completed row wrongly blocked the re-claim"


# ── AC-SUB-004 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_004_with_operation_run_inserts_running_and_heartbeats():
    """AC-SUB-004: with_operation_run inserts a running row and heartbeats it on interval."""
    import asyncio
    from libs.ops import with_operation_run
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")

    async def _run(fail: bool):
        db = await Database.connect(S._local_dsn())
        try:
            async with with_operation_run(db, "scope-hb", "meeting-harness",
                                          heartbeat_s=0.05) as handle:
                assert handle is not None
                await asyncio.sleep(0.2)  # > several heartbeat intervals
                if fail:
                    raise RuntimeError("boom")
        finally:
            await db.close()

    # Normal exit → completed, with an advanced heartbeat.
    asyncio.run(_run(fail=False))
    with S.pg_conn() as conn:
        row = conn.execute(
            "SELECT status, started_at, last_heartbeat_at FROM operation_runs "
            "WHERE scope_id='scope-hb' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None, "with_operation_run created no operation_runs row"
        assert row[0] == "completed", f"normal exit must complete the row; got {row[0]!r}"
        assert row[2] > row[1], "last_heartbeat_at must advance past started_at during the run"

    # Exception exit → failed.
    with pytest.raises(RuntimeError):
        asyncio.run(_run(fail=True))
    with S.pg_conn() as conn:
        row = conn.execute(
            "SELECT status FROM operation_runs "
            "WHERE scope_id='scope-hb' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "failed", f"exception exit must fail the row; got {row[0]!r}"


# ── AC-SUB-005 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_005_stale_row_swept_to_interrupted_on_read():
    """AC-SUB-005: a running row past STALE_AFTER_S is swept to interrupted lazily on read."""
    from libs.ops import run_reconcile_sweep  # noqa: F401  (red before product)
    import libs.db  # noqa: F401
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

        stale_after = 40  # STALE_AFTER_S ~ 40s (config/defaults.toml)
        try:
            stale_after = int(S.load_defaults_toml().get("ops", {}).get("stale_after_s", stale_after))
        except Exception:
            pass

        conn.execute("DELETE FROM operation_runs")
        # A fresh row (well within STALE_AFTER_S) and a stale one (past it).
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, last_heartbeat_at) "
            "VALUES ('fresh', 'meeting-harness', 'running', now())"
        )
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, last_heartbeat_at) "
            "VALUES ('stale', 'meeting-harness', 'running', now() - (%s || ' seconds')::interval)",
            (stale_after + 10,),
        )

        # Read the stale row via the product's reaper-on-read path.
        from libs.db import Database
        import asyncio
        db = asyncio.run(_reaper_read(Database, "stale"))
        del db  # touch to make intent explicit; the read itself is the sweep

        fresh = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='fresh'"
        ).fetchone()[0]
        stale = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='stale'"
        ).fetchone()[0]
        assert stale == "interrupted", f"stale running row must be swept to interrupted; got {stale!r}"
        assert fresh == "running", f"fresh row within STALE_AFTER_S must stay running; got {fresh!r}"


async def _reaper_read(Database, scope_id):
    """Enter the reaper-on-read path for one scope via the product Database facade."""
    db = await Database.connect(S._local_dsn())
    try:
        await db.get_operation_run(scope_id, "meeting-harness")
    finally:
        await db.close()
    return None


# ── AC-SUB-006 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_006_boot_bulk_sweep_marks_all_stale_interrupted():
    """AC-SUB-006: boot-time bulk sweep marks all stale running rows interrupted before routers mount."""
    import asyncio
    from libs.ops import run_reconcile_sweep  # noqa: F401
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")
        for i in range(3):
            conn.execute(
                "INSERT INTO operation_runs (scope_id, operation_type, status, last_heartbeat_at) "
                "VALUES (%s, 'meeting-harness', 'running', now() - interval '10 minutes')",
                (f"orphan-{i}",),
            )
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, last_heartbeat_at) "
            "VALUES ('warm', 'meeting-harness', 'running', now())"
        )

    async def _boot():
        db = await Database.connect(S._local_dsn())
        try:
            # The boot-time bulk sweep: reap every stale running row.
            await db.sweep_stale_operation_runs()
        finally:
            await db.close()

    asyncio.run(_boot())
    with S.pg_conn() as conn:
        left = conn.execute(
            "SELECT count(*) FROM operation_runs "
            "WHERE scope_id LIKE 'orphan-%' AND status='running'"
        ).fetchone()[0]
        assert left == 0, f"{left} orphaned running rows survived the boot sweep"
        warm = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='warm'"
        ).fetchone()[0]
        assert warm == "running", "the warm (fresh) row must not be swept"


# ── AC-SUB-007 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_007_fencing_heartbeat_running_keeps_ownership():
    """AC-SUB-007: heartbeat UPDATE gated on status='running' keeps ownership while owner."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")
        run_id = conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status) "
            "VALUES ('own', 'meeting-harness', 'running') RETURNING id"
        ).fetchone()[0]

        cur = conn.execute(
            "UPDATE operation_runs SET last_heartbeat_at=now() "
            "WHERE id=%s AND status='running'", (run_id,),
        )
        assert cur.rowcount == 1, (
            f"owner's fencing heartbeat on a running row must return rowcount 1; got {cur.rowcount}"
        )
        # rowcount 1 ⇒ is_owner stays True.
        is_owner = cur.rowcount == 1
        assert is_owner is True, "owner must retain is_owner while its row is running"


# ── AC-SUB-008 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_008_fencing_rowcount_zero_loses_ownership_and_self_terminates():
    """AC-SUB-008: fencing rowcount 0 (reclaimed) sets is_owner=False and self-terminates."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")
        run_id = conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status) "
            "VALUES ('reaped', 'meeting-harness', 'interrupted') RETURNING id"
        ).fetchone()[0]

        # The row is no longer status='running' (reclaimed/reaped).
        cur = conn.execute(
            "UPDATE operation_runs SET last_heartbeat_at=now() "
            "WHERE id=%s AND status='running'", (run_id,),
        )
        assert cur.rowcount == 0, (
            f"a reclaimed row's fencing heartbeat must return rowcount 0; got {cur.rowcount}"
        )
        is_owner = cur.rowcount == 1
        assert is_owner is False, "rowcount 0 must drive is_owner False (self-terminate)"

        # Exercise the product's own fencing helper if surfaced: it must map the
        # zero-rowcount heartbeat to is_owner False (the self-terminate signal).
        from libs.ops import OperationHandle
        assert hasattr(OperationHandle, "heartbeat") or hasattr(OperationHandle, "check_pause"), (
            "OperationHandle must surface the fencing/heartbeat seam"
        )


# ── AC-SUB-009 ────────────────────────────────────────────────────────────
@pytest.mark.security_adversarial
def test_sub_009_reclaimed_zombie_emits_nothing():
    """AC-SUB-009: a reclaimed zombie (is_owner False) refuses speak/send_chat/show_screen/apply/dispatch."""
    from services.harness import build_emitter  # spec-derived emit surface

    emitted = []

    def sink(*a, **k):
        emitted.append((a, k))

    # An emitter bound to a process whose row was reclaimed (is_owner False).
    emitter = build_emitter(is_owner=False, sink=sink)

    refused = 0
    for verb in ("speak", "send_chat", "show_screen", "apply", "dispatch"):
        fn = getattr(emitter, verb, None)
        assert callable(fn), f"emitter must expose the gated verb {verb!r}"
        try:
            result = fn("payload")
        except Exception:
            refused += 1  # raising is an acceptable refusal
            continue
        # A non-raising verb must signal refusal (falsy / not delivered).
        assert not result, f"{verb} must refuse (return falsy) when is_owner is False; got {result!r}"
        refused += 1

    assert refused == 5, "all five emit verbs must be gated on is_owner"
    assert emitted == [], (
        f"a reclaimed zombie must reach the wire zero times; leaked {len(emitted)} emissions"
    )


# ── AC-SUB-010 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_010_check_pause_surfaces_pause_requested():
    """AC-SUB-010: check_pause() surfaces pause_requested so a running build can be paused/aborted."""
    import asyncio
    from libs.ops import with_operation_run
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")

    async def _run():
        db = await Database.connect(S._local_dsn())
        try:
            async with with_operation_run(db, "pause-scope", "workroom:1") as handle:
                # pause_requested defaults false → check_pause() false.
                assert (await handle.check_pause()) is False, "check_pause must be False by default"
                # A human sets pause_requested true on the row.
                with S.pg_conn() as c2:
                    c2.execute(
                        "UPDATE operation_runs SET pause_requested=true "
                        "WHERE scope_id='pause-scope' AND status='running'"
                    )
                assert (await handle.check_pause()) is True, (
                    "check_pause() must return True once pause_requested is set"
                )
        finally:
            await db.close()

    asyncio.run(_run())


# ── AC-SUB-011 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_011_atomic_claim_non_null_returner_owns():
    """AC-SUB-011: the atomic-claim non-null returner owns the meeting."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")

        claim = (
            "INSERT INTO operation_runs (scope_id, operation_type, status) "
            "VALUES (%s, 'meeting-harness', 'running') "
            "ON CONFLICT (scope_id, operation_type) WHERE status='running' "
            "DO NOTHING RETURNING id"
        )
        got = conn.execute(claim, ("meeting-1",)).fetchone()
        assert got is not None and got[0] is not None, (
            "the first atomic claim must return a non-null id (the owner)"
        )
        running = conn.execute(
            "SELECT count(*) FROM operation_runs "
            "WHERE scope_id='meeting-1' AND status='running'"
        ).fetchone()[0]
        assert running == 1, "exactly one running row must exist after the claim"


# ── AC-SUB-012 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_012_concurrent_duplicate_claim_exactly_one_owner():
    """AC-SUB-012: concurrent duplicate join events yield exactly one owner; the loser backs off."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")

    import psycopg
    claim = (
        "INSERT INTO operation_runs (scope_id, operation_type, status) "
        "VALUES ('meeting-dup', 'meeting-harness', 'running') "
        "ON CONFLICT (scope_id, operation_type) WHERE status='running' "
        "DO NOTHING RETURNING id"
    )
    # Two independent connections race the same claim (concurrency schedule).
    dsn = S._local_dsn()
    c1 = psycopg.connect(dsn, autocommit=True)
    c2 = psycopg.connect(dsn, autocommit=True)
    try:
        r1 = c1.execute(claim).fetchone()
        r2 = c2.execute(claim).fetchone()
    finally:
        c1.close()
        c2.close()

    winners = [x for x in (r1, r2) if x is not None and x[0] is not None]
    losers = [x for x in (r1, r2) if x is None]
    assert len(winners) == 1, f"exactly one process must win; winners={winners}"
    assert len(losers) == 1, f"the other must get null and back off; got {(r1, r2)}"

    with S.pg_conn() as conn:
        running = conn.execute(
            "SELECT count(*) FROM operation_runs "
            "WHERE scope_id='meeting-dup' AND status='running'"
        ).fetchone()[0]
        assert running == 1, f"only one running row may exist for the meeting; got {running}"


# ── AC-SUB-013 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_sub_013_coordination_uses_postgres_never_broker_or_inmemory_lock():
    """AC-SUB-013: cross-process coordination uses PG atomic claim/advisory lock, never in-memory lock or broker."""
    # Coordination must be evidenced by Postgres primitives.
    pg_coord = S.grep_python(r"pg_advisory_(xact_)?lock|ON CONFLICT.*DO NOTHING", flags=re.I)
    assert pg_coord, "no Postgres atomic-claim / advisory-lock coordination site found (product not built)"

    # No broker import anywhere in the product source.
    broker = S.grep_python(r"^\s*(import|from)\s+(redis|aioredis|pika|kombu|celery|kafka|aiokafka|nats|rabbitmq)\b")
    assert not broker, f"cross-process coordination must not use a broker: {broker}"

    # No in-memory lock used for cross-process coordination: an asyncio.Lock /
    # threading.Lock guarding a claim/coordination path is forbidden. We flag any
    # module that both coordinates AND holds an in-process lock as a proxy.
    lock_sites = S.grep_python(r"asyncio\.Lock\(|threading\.Lock\(|multiprocessing\.Lock\(")
    coord_modules = {relpath for relpath, _l, _t in pg_coord}
    coord_modules |= {relpath for relpath, _l, _t in
                      S.grep_python(r"claim_meeting|atomic_claim|meeting-harness", flags=re.I)}
    offenders = [h for h in lock_sites if h[0] in coord_modules]
    assert not offenders, (
        f"cross-process coordination modules must not use an in-memory lock: {offenders}"
    )


# ── AC-SUB-014 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_014_advisory_lock_serializes_per_meeting_critical_section():
    """AC-SUB-014: a per-meeting critical section holds pg_advisory_xact_lock and serializes on the locked connection."""
    import libs.db  # noqa: F401  (red before product)

    # Static half: the section must acquire pg_advisory_xact_lock(hashtext(...),0).
    lock_use = S.grep_python(r"pg_advisory_xact_lock\s*\(\s*hashtext", flags=re.I)
    assert lock_use, "per-meeting critical section must use pg_advisory_xact_lock(hashtext($1),0)"

    # Behavioural half: two connections cannot hold the same advisory-xact-lock at once.
    import psycopg
    dsn = S._local_dsn()
    r = S.apply_migrations(dsn or "")
    assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

    c1 = psycopg.connect(dsn)   # NOT autocommit: xact lock lives for the tx
    c2 = psycopg.connect(dsn)
    try:
        c1.execute("SELECT pg_advisory_xact_lock(hashtext('m-crit'), 0)")
        # A second connection must NOT be able to take the same lock without blocking.
        got = c2.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext('m-crit'), 0)"
        ).fetchone()[0]
        assert got is False, (
            "a concurrent invocation must serialize (second try_advisory_xact_lock must fail while held)"
        )
        c1.rollback()  # release the xact lock
        got2 = c2.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext('m-crit'), 0)"
        ).fetchone()[0]
        assert got2 is True, "the lock must be acquirable once the holder's transaction ends"
        c2.rollback()
    finally:
        c1.close()
        c2.close()


# ── AC-SUB-015 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_sub_015_sandbox_only_idempotent_verbs_no_state_machine():
    """AC-SUB-015: the sandbox provider keeps only {provision, destroy, health_check}; no ManagedResource FSM."""
    from libs.ops import sandbox_provider  # noqa: F401  (red before product)

    # The provider exposes exactly the three idempotent verbs.
    for verb in ("provision", "destroy", "health_check"):
        defs = S.count_def_sites(verb)
        assert defs, f"sandbox provider must define {verb!r}"

    # No ManagedResource state machine / provisioning-running-stopped-failed FSM.
    fsm = S.grep_python(r"\bManagedResource\b|provisioning.*running.*stopped|stuck.?provision", flags=re.I)
    assert not fsm, f"ManagedResource state-machine / stuck-provision recovery must be dropped: {fsm}"
    fsm_class = S.count_def_sites("ManagedResource")
    assert not fsm_class, f"no ManagedResource class allowed: {fsm_class}"


# ── AC-SUB-016 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_016_sandbox_bounded_by_timeout_destroy_and_ttl_reconcile():
    """AC-SUB-016: every sandbox is bounded by E2B timeout + explicit destroy on meeting-end + TTL reconcile."""
    from libs.ops import sandbox_provider
    from libs.ops import run_reconcile_sweep  # noqa: F401

    # (1) provision sets an E2B-native timeout backstop.
    prov_src = ""
    for relpath in S.count_def_sites("provision"):
        prov_src += S.read_text(*relpath.split("/")) or ""
    assert re.search(r"timeout", prov_src, re.I), (
        "provision must set an E2B-native sandbox timeout backstop"
    )
    assert hasattr(sandbox_provider, "provision") and hasattr(sandbox_provider, "destroy"), (
        "sandbox_provider must expose provision + destroy"
    )

    # (2) meeting-end triggers an explicit destroy (the ordered close).
    close_destroy = S.grep_python(r"\.destroy\s*\(|sandbox_provider\.destroy", flags=re.I)
    assert close_destroy, "meeting-end close must call an explicit sandbox destroy"

    # (3) the reconcile-cron kills any sandbox found past its TTL.
    recon_src = S.read_all_text("*.py", root_parts=("libs", "ops"))
    assert re.search(r"ttl", recon_src, re.I) and re.search(r"destroy|kill", recon_src, re.I), (
        "run_reconcile_sweep must destroy sandboxes past their TTL"
    )


# ── AC-SUB-017 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_sub_017_preprovision_event_driven_no_warm_pool():
    """AC-SUB-017: pre-provision is join/creation-event driven; no warm sandbox pool."""
    # Pre-provision must be keyed to a creation/join event.
    preprov = S.grep_python(r"pre_?provision|preprovision", flags=re.I)
    assert preprov, "no pre-provision logic found (product not built)"

    # No warm pool structure: no pool of idle sandboxes maintained.
    pool = S.grep_python(r"warm_?pool|sandbox_pool|idle_sandbox|pool_of_sandboxes", flags=re.I)
    assert not pool, f"no warm pool of idle sandboxes may be maintained: {pool}"


# ── AC-SUB-018 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_018_reconcile_idempotent_token_gated_outside_auth_wall():
    """AC-SUB-018: run_reconcile_sweep() is idempotent, token-gated at /internal/reconcile outside the auth wall."""
    from libs.ops import run_reconcile_sweep  # noqa: F401

    # POST /internal/reconcile requires the internal token, mounted outside the auth wall.
    route_hits = S.grep_python(r"/internal/reconcile", flags=re.I)
    assert route_hits, "no POST /internal/reconcile route found"
    token_gate = S.grep_python(r"internal.?token|X-Internal-Token|reconcile.*token", flags=re.I)
    assert token_gate, "reconcile route must be token-gated"

    # Prod scheduler (5 min) + dev in-process interval call the SAME function.
    same_fn = S.grep_python(r"run_reconcile_sweep")
    assert len(same_fn) >= 2, (
        f"prod scheduler and dev interval must both call run_reconcile_sweep (one function); found {same_fn}"
    )

    # Idempotency: running the sweep twice against the same state yields the same end state.
    import asyncio
    from libs.ops import run_reconcile_sweep as sweep
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, last_heartbeat_at) "
            "VALUES ('stale-recon', 'meeting-harness', 'running', now() - interval '10 minutes')"
        )

    async def _sweep():
        db = await Database.connect(S._local_dsn())
        try:
            await sweep(db)
        finally:
            await db.close()

    asyncio.run(_sweep())
    with S.pg_conn() as conn:
        first = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='stale-recon'"
        ).fetchone()[0]
    asyncio.run(_sweep())  # second run, same state
    with S.pg_conn() as conn:
        second = conn.execute(
            "SELECT status FROM operation_runs WHERE scope_id='stale-recon'"
        ).fetchone()[0]
    assert first == second == "interrupted", (
        f"reconcile must be idempotent (same end state across runs); got {first!r} then {second!r}"
    )


# ── AC-SUB-019 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_sub_019_availability_loops_on_in_process_interval_not_cron():
    """AC-SUB-019: availability-critical loops (STT cred refresh) stay on an in-process interval, not reconcile cron."""
    # The STT-cred-refresh loop must exist and run on an in-process interval.
    stt = S.grep_python(r"stt.*refresh|refresh.*stt|refresh.*cred", flags=re.I)
    assert stt, "no STT cred-refresh loop found (product not built)"

    # It must not be scheduled by the scale-to-zero reconcile cron.
    stt_modules = {relpath for relpath, _l, _t in stt}
    for relpath in stt_modules:
        src = S.read_text(*relpath.split("/")) or ""
        assert "/internal/reconcile" not in src and "run_reconcile_sweep" not in src, (
            f"STT refresh in {relpath} must not ride the reconcile cron"
        )
    # And an in-process interval mechanism must be present for the loop.
    interval = S.grep_python(r"asyncio\.sleep|create_task|interval", flags=re.I)
    assert any(h[0] in stt_modules for h in interval), (
        "STT refresh must run on an in-process interval (asyncio loop), not the cron"
    )


# ── AC-SUB-020 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_020_workroom_task_restarts_unless_deliverable_exists():
    """AC-SUB-020: a workroom task restarts unless a SQL completion check shows its deliverable exists."""
    import asyncio
    from services.workroom import recover_task
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")
        # An interrupted workroom task whose deliverable already exists.
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, result_ref) "
            "VALUES ('task-done', 'workroom:done', 'interrupted', '{\"deliverable\": \"gs://x\"}'::jsonb)"
        )
        # An interrupted workroom task with no deliverable.
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, result_ref) "
            "VALUES ('task-todo', 'workroom:todo', 'interrupted', NULL)"
        )

    async def _recover(scope, op):
        db = await Database.connect(S._local_dsn())
        try:
            return await recover_task(db, scope, op)
        finally:
            await db.close()

    # Deliverable exists → NOT re-run.
    done = asyncio.run(_recover("task-done", "workroom:done"))
    assert getattr(done, "restarted", done) in (False, "skipped") or done is False, (
        f"a task whose deliverable exists must not be re-run; got {done!r}"
    )
    # No deliverable → restart the coarse unit.
    todo = asyncio.run(_recover("task-todo", "workroom:todo"))
    assert getattr(todo, "restarted", todo) in (True, "restarted") or todo is True, (
        f"a task with no deliverable must be restarted; got {todo!r}"
    )


# ── AC-SUB-021 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_021_meeting_harness_crash_restart_not_resume():
    """AC-SUB-021: a meeting-harness crash is restart-not-resume (re-join Recall, replay from progress)."""
    import asyncio
    from services.harness import recover_meeting_harness
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")
        conn.execute(
            "INSERT INTO operation_runs (scope_id, operation_type, status, progress) "
            "VALUES ('m-crash', 'meeting-harness', 'interrupted', "
            "'{\"transcript_offset\": 42}'::jsonb)"
        )

    async def _recover():
        db = await Database.connect(S._local_dsn())
        try:
            return await recover_meeting_harness(db, "m-crash")
        finally:
            await db.close()

    plan = asyncio.run(_recover())
    # Restart-not-resume: re-join via Recall and replay from persisted progress.
    assert getattr(plan, "rejoin_recall", None) is True or getattr(plan, "action", "") == "rejoin", (
        f"recovery must re-join via Recall (restart), not resume the dead media session; got {plan!r}"
    )
    assert getattr(plan, "resume_media_session", False) is False, (
        "recovery must NOT resume the dead media session"
    )
    replayed = getattr(plan, "replay_from", None)
    assert replayed in (42, {"transcript_offset": 42}) or getattr(plan, "checkpoint_resume", False) is False, (
        "recovery must replay from persisted progress, not fine-grained checkpoint-resume"
    )


# ── AC-SUB-022 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_022_webhook_events_dedupe_by_delivery_guid():
    """AC-SUB-022: webhook_events dedupes by delivery_guid; duplicate delivery is a no-op."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        assert S.table_exists(conn, "webhook_events"), "webhook_events table must exist"

        cols = S.table_columns(conn, "webhook_events")
        assert "delivery_guid" in cols, f"webhook_events must have delivery_guid; got {set(cols)}"

        conn.execute("DELETE FROM webhook_events")
        insert = (
            "INSERT INTO webhook_events (delivery_guid, status) "
            "VALUES (%s, 'pending') ON CONFLICT (delivery_guid) DO NOTHING"
        )
        conn.execute(insert, ("guid-1",))
        conn.execute(insert, ("guid-1",))  # duplicate delivery → no-op

        n = conn.execute(
            "SELECT count(*) FROM webhook_events WHERE delivery_guid='guid-1'"
        ).fetchone()[0]
        assert n == 1, f"duplicate delivery_guid must yield exactly one row; got {n}"


# ── AC-SUB-023 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_023_webhook_returns_200_after_insert_then_drains():
    """AC-SUB-023: webhook ingest returns 200 immediately after INSERT, then drains pending on boot + periodically."""
    from services.harness import ingest_webhook, drain_pending_webhooks

    order = []

    class _Recorder:
        def insert(self, *a, **k):
            order.append("insert")
            return True

        def process(self, *a, **k):
            order.append("process")

    rec = _Recorder()
    status = ingest_webhook({"delivery_guid": "g-23", "body": {}}, store=rec)
    # 200 returned immediately after the durable INSERT, before processing.
    assert status == 200, f"webhook ingest must return 200; got {status}"
    assert order == ["insert"], (
        f"processing must NOT run before the 200 is returned; order={order}"
    )

    # Pending rows are drained on boot + periodically (idempotent processing).
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM webhook_events")
        conn.execute(
            "INSERT INTO webhook_events (delivery_guid, status) VALUES ('g-drain', 'pending')"
        )

    import asyncio
    from libs.db import Database

    async def _drain():
        db = await Database.connect(S._local_dsn())
        try:
            await drain_pending_webhooks(db)
        finally:
            await db.close()

    asyncio.run(_drain())
    with S.pg_conn() as conn:
        left = conn.execute(
            "SELECT count(*) FROM webhook_events WHERE status='pending'"
        ).fetchone()[0]
        assert left == 0, f"pending webhooks must be drained; {left} left pending"


# ── AC-SUB-024 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_sub_024_no_general_meeting_events_bus():
    """AC-SUB-024: no general meeting_events bus exists; webhook_events is the only external-callback durability."""
    import libs.db  # noqa: F401  (red before product)
    # Schema: no meeting_events table.
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        assert not S.table_exists(conn, "meeting_events"), "no meeting_events table may exist"

    # Code: no general event-bus mechanism / meeting_events reference.
    bus = S.grep_python(r"meeting_events|event_bus|EventBus|meeting_event_bus", flags=re.I)
    assert not bus, f"no general meeting_events bus may exist: {bus}"


# ── AC-SUB-025 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_sub_025_meeting_cost_canonical_columns_and_meetings_fk():
    """AC-SUB-025: meeting_cost has the canonical persisted column set; meeting_id uuid REFERENCES meetings(id)."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

        cols = S.table_columns(conn, "meeting_cost")
        assert set(cols) == _MEETING_COST_COLS, (
            f"meeting_cost columns != canonical: "
            f"extra={set(cols) - _MEETING_COST_COLS}, missing={_MEETING_COST_COLS - set(cols)}"
        )
        assert cols["meeting_id"] == "uuid", f"meeting_id must be uuid; got {cols['meeting_id']!r}"
        # The FK edge giving tenant reachability (A-009): meeting_id → meetings(id).
        fks = _fk_targets(conn, "meeting_cost", "meeting_id")
        assert ("meetings", "id") in fks, (
            f"meeting_cost.meeting_id must be a FK REFERENCES meetings(id); got {fks}"
        )


# ── AC-SUB-026 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_026_recycled_orchestrator_reloads_spent_cost():
    """AC-SUB-026: a recycled orchestrator reloads spent cost from meeting_cost, never resets to 0."""
    import asyncio
    from services.harness import check_meeting_budget
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        # A meeting with accrued spend already persisted.
        mid = conn.execute(
            "INSERT INTO meetings (tenant_id, repo_id, status) "
            "VALUES (NULL, NULL, 'live') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO meeting_cost (meeting_id, model_usd) VALUES (%s, 3.50)", (mid,)
        )

    async def _recycle():
        db = await Database.connect(S._local_dsn())
        try:
            # Recycle: a fresh orchestrator re-claims and reads the budget.
            return await check_meeting_budget(db, mid)
        finally:
            await db.close()

    spend = asyncio.run(_recycle())
    val = getattr(spend, "spent_usd", None)
    if val is None:
        val = spend if isinstance(spend, (int, float)) else getattr(spend, "model_usd", None)
    assert val is not None and float(val) >= 3.50, (
        f"post-recycle budget must read the persisted spend (>=3.50), never 0; got {spend!r}"
    )
    assert float(val) != 0.0, "cost must not be reset to 0 at the recovery moment"


# ── AC-SUB-027 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_027_staged_drafts_persisted_at_creation_with_meetings_fk():
    """AC-SUB-027: staged_drafts persists the draft at creation (GCS Object-Versioned + a proposed row); meetings FK."""
    import asyncio
    from services.workroom import propose_change
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

        # Schema: canonical columns + meeting_id uuid FK → meetings(id).
        cols = S.table_columns(conn, "staged_drafts")
        assert set(cols) == _STAGED_DRAFT_COLS, (
            f"staged_drafts columns != canonical: "
            f"extra={set(cols) - _STAGED_DRAFT_COLS}, missing={_STAGED_DRAFT_COLS - set(cols)}"
        )
        assert cols["meeting_id"] == "uuid", f"meeting_id must be uuid; got {cols['meeting_id']!r}"
        fks = _fk_targets(conn, "staged_drafts", "meeting_id")
        assert ("meetings", "id") in fks, (
            f"staged_drafts.meeting_id must be a FK REFERENCES meetings(id); got {fks}"
        )
        mid = conn.execute(
            "INSERT INTO meetings (tenant_id, repo_id, status) "
            "VALUES (NULL, NULL, 'live') RETURNING id"
        ).fetchone()[0]

    async def _propose():
        db = await Database.connect(S._local_dsn())
        try:
            return await propose_change(db, meeting_id=mid, kind="notes-edit",
                                        summary="s", content="the full draft body")
        finally:
            await db.close()

    draft = asyncio.run(_propose())
    with S.pg_conn() as conn:
        row = conn.execute(
            "SELECT status, artifact_ref FROM staged_drafts WHERE meeting_id=%s", (mid,)
        ).fetchone()
        assert row is not None, "propose_change must persist a staged_drafts row at creation"
        assert row[0] == "proposed", f"the row status must be 'proposed'; got {row[0]!r}"
        # Full content written to GCS Object-Versioned, referenced by artifact_ref.
        assert row[1], "artifact_ref must reference the GCS Object-Versioned draft content"
    assert draft is not None


# ── AC-SUB-028 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_028_human_accept_reads_persisted_draft_after_teardown():
    """AC-SUB-028: a human accepting after the call reads the persisted draft (survives sandbox teardown)."""
    import asyncio
    from services.workroom import propose_change, accept_draft
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        mid = conn.execute(
            "INSERT INTO meetings (tenant_id, repo_id, status) "
            "VALUES (NULL, NULL, 'ended') RETURNING id"
        ).fetchone()[0]

    async def _propose_then_accept():
        db = await Database.connect(S._local_dsn())
        try:
            draft = await propose_change(db, meeting_id=mid, kind="notes-edit",
                                         summary="s", content="durable body")
            draft_id = getattr(draft, "draft_id", None) or draft
            # The sandbox review_session is now torn down — accept must read
            # from durable storage (GCS + row), not the dead in-memory session.
            return await accept_draft(db, draft_id, review_session=None)
        finally:
            await db.close()

    result = asyncio.run(_propose_then_accept())
    applied = getattr(result, "applied", None)
    if applied is None:
        applied = getattr(result, "content", None) or result
    assert applied, "accept-handler must apply from the persisted draft after teardown"
    assert getattr(result, "read_from", "durable") != "session", (
        "accept must not read the dead in-memory review session"
    )


# ── AC-SUB-029 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_sub_029_workroom_and_close_reuse_operation_runs_no_new_tables():
    """AC-SUB-029: workroom task + meeting-close reuse operation_runs; no workroom_tasks/close_jobs tables."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        assert not S.table_exists(conn, "workroom_tasks"), "no workroom_tasks table may exist"
        assert not S.table_exists(conn, "close_jobs"), "no close_jobs table may exist"

    # Workroom task and meeting-close are operation_runs rows (op_type literals).
    workroom_op = S.grep_python(r"['\"]workroom:", flags=re.I)
    assert workroom_op, "a workroom task must be represented as operation_type='workroom:<id>'"
    close_op = S.grep_python(r"['\"]meeting-close['\"]")
    assert close_op, "a meeting close must be represented as operation_type='meeting-close'"


# ── AC-SUB-030 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_sub_030_identity_schema_five_tables_with_tenant_fks():
    """AC-SUB-030: identity/tenancy schema is exactly {tenants, users, repos, meetings, sessions} with canonical columns."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

        for t in ("tenants", "users", "repos", "meetings", "sessions"):
            assert S.table_exists(conn, t), f"identity table {t!r} must exist"

        # meetings.status domain {live, ended, interrupted}; pinned_sha exists.
        mcols = S.table_columns(conn, "meetings")
        assert "pinned_sha" in mcols, f"meetings.pinned_sha must exist; got {set(mcols)}"
        assert "status" in mcols and "tenant_id" in mcols and "repo_id" in mcols

        # status default is 'live' and only the three domain values are valid.
        mid = conn.execute(
            "INSERT INTO meetings (tenant_id, repo_id) VALUES (NULL, NULL) RETURNING id, status"
        ).fetchone()
        assert mid[1] == "live", f"meetings.status must default 'live'; got {mid[1]!r}"
        # Each of the three domain values is accepted.
        for st in ("live", "ended", "interrupted"):
            conn.execute("UPDATE meetings SET status=%s WHERE id=%s", (st, mid[0]))

        # users / repos / meetings carry tenant_id.
        for t in ("users", "repos", "meetings"):
            assert "tenant_id" in S.table_columns(conn, t), f"{t} must carry tenant_id"


# ── AC-SUB-031 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_031_signin_creates_user_and_signed_session_resolves():
    """AC-SUB-031: sign-in creates/loads a users row + signed session cookie; resolve_session→{user_id, tenant_id}."""
    import asyncio
    from services.harness import complete_signin, resolve_session
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"

    async def _flow():
        db = await Database.connect(S._local_dsn())
        try:
            # Google-OAuth sign-in completes → users row + signed session cookie.
            session = await complete_signin(db, email="a@example.com")
            cookie = getattr(session, "cookie", None) or session
            resolved = await resolve_session(db, {"session": cookie})
            return cookie, resolved
        finally:
            await db.close()

    cookie, resolved = asyncio.run(_flow())
    uid = resolved["user_id"] if isinstance(resolved, dict) else getattr(resolved, "user_id")
    tid = resolved["tenant_id"] if isinstance(resolved, dict) else getattr(resolved, "tenant_id")
    assert uid is not None and tid is not None, (
        f"resolve_session must return {{user_id, tenant_id}}; got {resolved!r}"
    )
    with S.pg_conn() as conn:
        n = conn.execute(
            "SELECT count(*) FROM users WHERE email='a@example.com'"
        ).fetchone()[0]
        assert n == 1, "sign-in must create exactly one users row"

    # An unsigned/tampered cookie must NOT resolve.
    async def _tampered():
        db = await Database.connect(S._local_dsn())
        try:
            return await resolve_session(db, {"session": str(cookie) + "TAMPER"})
        finally:
            await db.close()

    tampered = asyncio.run(_tampered())
    assert not tampered, "an unsigned/tampered session cookie must not resolve"


# ── AC-SUB-032 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_032_invite_creates_meeting_and_bot_id_round_trips():
    """AC-SUB-032: invite creates a meetings row bound to (tenant, repo, pinned_sha=HEAD) and binds Recall bot_id."""
    import asyncio
    from services.harness import invite_proxy, resolve_bot_id
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        tid = conn.execute(
            "INSERT INTO tenants (name) VALUES ('t') RETURNING id"
        ).fetchone()[0]
        rid = conn.execute(
            "INSERT INTO repos (tenant_id, full_name, default_branch) "
            "VALUES (%s, 'o/r', 'main') RETURNING id", (tid,)
        ).fetchone()[0]

    async def _invite():
        db = await Database.connect(S._local_dsn())
        try:
            meeting = await invite_proxy(db, tenant_id=tid, repo_id=rid,
                                         meeting_url="https://meet/x", head_sha="deadbeef")
            mid = getattr(meeting, "id", None) or (meeting.get("id") if isinstance(meeting, dict) else meeting)
            return mid
        finally:
            await db.close()

    mid = asyncio.run(_invite())
    with S.pg_conn() as conn:
        row = conn.execute(
            "SELECT tenant_id, repo_id, pinned_sha, recall_bot_id FROM meetings WHERE id=%s", (mid,)
        ).fetchone()
        assert row is not None, "invite must create a meetings row"
        assert str(row[0]) == str(tid) and str(row[1]) == str(rid), "meeting must bind (tenant, repo)"
        assert row[2] == "deadbeef", f"pinned_sha must be HEAD; got {row[2]!r}"
        assert row[3], "recall_bot_id must be written back after the bot launches"
        bot_id = row[3]

    async def _resolve():
        db = await Database.connect(S._local_dsn())
        try:
            return await resolve_bot_id(db, bot_id)
        finally:
            await db.close()

    resolved = asyncio.run(_resolve())
    r_mid = resolved["meeting_id"] if isinstance(resolved, dict) else getattr(resolved, "meeting_id", getattr(resolved, "id", None))
    assert str(r_mid) == str(mid), "the webhook's bot_id must resolve back to the meetings row (→ tenant + repo)"


# ── AC-SUB-033 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_033_model_spend_written_by_scribe_and_seam_meter():
    """AC-SUB-033: model spend writes meeting_cost.model_usd from both the Scribe and the seam meter (cache split)."""
    import asyncio
    from services.scribe import record_scribe_cost
    from services.harness import record_seam_cost
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        mid = conn.execute(
            "INSERT INTO meetings (tenant_id, repo_id, status) "
            "VALUES (NULL, NULL, 'live') RETURNING id"
        ).fetchone()[0]

    async def _accrue():
        db = await Database.connect(S._local_dsn())
        try:
            # The Scribe (bare Messages call) increments model_usd + records the cache split.
            await record_scribe_cost(db, meeting_id=mid, model_usd=1.0,
                                     cache_read_usd=0.2, cache_creation_usd=0.1)
            # The seam-based meter (wakes + Workroom) also increments model_usd.
            await record_seam_cost(db, meeting_id=mid, model_usd=2.0)
        finally:
            await db.close()

    asyncio.run(_accrue())
    with S.pg_conn() as conn:
        row = conn.execute(
            "SELECT model_usd, cache_read_usd, cache_creation_usd "
            "FROM meeting_cost WHERE meeting_id=%s", (mid,)
        ).fetchone()
        assert row is not None, "both writers must converge on a meeting_cost row"
        assert float(row[0]) >= 3.0, (
            f"model_usd must be incremented by BOTH the Scribe and the seam meter; got {row[0]}"
        )
        assert float(row[1]) >= 0.2 and float(row[2]) >= 0.1, (
            "the Scribe must record the cache_read / cache_creation split"
        )


# ── AC-SUB-034 ────────────────────────────────────────────────────────────
@pytest.mark.model_stateful
def test_sub_034_transcript_status_default_pending_flips_atomically():
    """AC-SUB-034: transcript_segments.status defaults 'pending' and flips to 'comprehended' transactionally with the delta."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        assert S.table_exists(conn, "transcript_segments"), "transcript_segments table must exist"

        # A fresh segment has status DEFAULT 'pending'.
        default = conn.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='transcript_segments' AND column_name='status'"
        ).fetchone()
        assert default is not None and default[0] is not None and "pending" in default[0], (
            f"transcript_segments.status must DEFAULT 'pending'; got {default!r}"
        )

    # The flip to 'comprehended' happens in the SAME transaction as the note-delta
    # append (never separately): a rolled-back tx must leave status 'pending'.
    import asyncio
    from libs.db import Database
    from services.scribe import apply_note_delta

    with S.pg_conn() as conn:
        seg = conn.execute(
            "INSERT INTO transcript_segments (meeting_id, text) "
            "VALUES (NULL, 'hi') RETURNING id, status"
        ).fetchone()
        assert seg[1] == "pending", f"fresh segment must be 'pending'; got {seg[1]!r}"
        seg_id = seg[0]

    async def _apply(fail):
        db = await Database.connect(S._local_dsn())
        try:
            await apply_note_delta(db, segment_id=seg_id, delta="note", _fail_after_flip=fail)
        finally:
            await db.close()

    # A failure after the flip but before commit must roll BOTH back (atomic).
    with pytest.raises(Exception):
        asyncio.run(_apply(fail=True))
    with S.pg_conn() as conn:
        st = conn.execute(
            "SELECT status FROM transcript_segments WHERE id=%s", (seg_id,)
        ).fetchone()[0]
        assert st == "pending", (
            "flip + delta-append must be one transaction; a rollback must leave status 'pending'"
        )

    # A clean apply commits both together.
    asyncio.run(_apply(fail=False))
    with S.pg_conn() as conn:
        st = conn.execute(
            "SELECT status FROM transcript_segments WHERE id=%s", (seg_id,)
        ).fetchone()[0]
        assert st == "comprehended", f"a committed apply must flip to 'comprehended'; got {st!r}"


# ── AC-SUB-035 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_sub_035_emit_frontier_complete_every_verb_gates_on_is_owner():
    """AC-SUB-035: the emit frontier {speak,send_chat,show_screen,apply,dispatch} is complete; every member gates on is_owner."""
    # Every delivery-authority / side-effect verb must be defined in the harness.
    for verb in sorted(_EMIT_FRONTIER):
        defs = S.count_def_sites(verb)
        assert defs, f"delivery/side-effect verb {verb!r} must be defined (frontier incomplete without it)"

    # In particular show_screen (§12.3) must be present and gated — the verb
    # AC-SUB-009's original four-verb list omitted.
    show = S.count_def_sites("show_screen")
    assert show, "show_screen must be an enumerated, gated delivery-authority verb"

    # Every frontier verb's body must reference the is_owner gate.
    ungated = []
    for verb in sorted(_EMIT_FRONTIER):
        gated = False
        for relpath in S.count_def_sites(verb):
            src = S.read_text(*relpath.split("/")) or ""
            # find the def block for this verb and check it references is_owner
            m = re.search(rf"def\s+{re.escape(verb)}\b.*?(?=\n\S|\Z)", src, re.S)
            body = m.group(0) if m else src
            if re.search(r"is_owner", body):
                gated = True
        if not gated:
            ungated.append(verb)
    assert not ungated, f"these outward verbs reach the wire without an is_owner gate: {ungated}"

    # The gated set must be enumerated as explicitly as it is gated: a canonical
    # frontier declaration must exist so a new ungated verb fails this check.
    frontier_decl = S.grep_python(r"EMIT_FRONTIER|DELIVERY_VERBS|GATED_EMIT|is_owner_gated", flags=re.I)
    assert frontier_decl, (
        "the emit frontier must be explicitly enumerated (a canonical gated-verb set) "
        "so a new outward verb added outside it fails static completeness"
    )


# ── AC-SUB-036 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_036_claim_populates_created_by_with_owner_instance_id():
    """AC-SUB-036: the claim/with_operation_run path populates operation_runs.created_by with the owner instance-id."""
    import asyncio
    from libs.ops import with_operation_run
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")

    instance_id = "instance-abc-123"

    async def _claim():
        db = await Database.connect(S._local_dsn(), instance_id=instance_id)
        try:
            async with with_operation_run(db, "scope-cb", "meeting-harness"):
                await asyncio.sleep(0)
        finally:
            await db.close()

    asyncio.run(_claim())
    with S.pg_conn() as conn:
        created_by = conn.execute(
            "SELECT created_by FROM operation_runs "
            "WHERE scope_id='scope-cb' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
        assert created_by is not None, (
            "created_by must never be NULL on a successfully-claimed running row"
        )
        assert created_by == instance_id, (
            f"created_by must equal the claiming instance-id (read by WS affinity, AC-OBS-007); "
            f"got {created_by!r} != {instance_id!r}"
        )


# ── AC-SUB-037 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_sub_037_operation_runs_status_domain_pinned():
    """AC-SUB-037: operation_runs.status is exactly {running,completed,failed,interrupted}; out-of-domain rejected/unreachable."""
    import libs.db  # noqa: F401  (red before product)
    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")

        # Each in-domain value is accepted.
        for st in sorted(_OPRUN_STATUS_DOMAIN):
            conn.execute(
                "INSERT INTO operation_runs (scope_id, operation_type, status) "
                "VALUES (%s, %s, %s)", (f"s-{st}", f"t-{st}", st),
            )

        # An out-of-domain status must be rejected by a constraint, OR be
        # provably unreachable (no write path emits an out-of-domain literal).
        rejected = False
        try:
            conn.execute(
                "INSERT INTO operation_runs (scope_id, operation_type, status) "
                "VALUES ('s-bad', 't-bad', 'in_meeting')"
            )
        except Exception:
            rejected = True

        if not rejected:
            # No DB constraint → the code path must never write an out-of-domain
            # literal. Enumerate every status literal the product writes.
            writes = S.grep_python(r"status\s*=\s*['\"](\w+)['\"]|['\"]status['\"]\s*[:=]\s*['\"](\w+)['\"]")
            written = set()
            for _rel, _ln, line in writes:
                for m in re.finditer(r"['\"](running|completed|failed|interrupted|in_meeting|paused|queued|succeeded)['\"]", line):
                    written.add(m.group(1))
            out_of_domain = written - _OPRUN_STATUS_DOMAIN
            assert not out_of_domain, (
                f"no DB constraint AND the code writes out-of-domain status literals: {out_of_domain}"
            )
        else:
            conn.execute("DELETE FROM operation_runs WHERE scope_id='s-bad'")


# ── AC-SUB-038 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_sub_038_heartbeat_bumps_sandbox_activity_every_tick():
    """AC-SUB-038: heartbeat loop bumps sandbox activity every tick (survives silent agent work)."""
    import asyncio
    from libs.ops import with_operation_run
    from libs.db import Database

    with S.pg_conn() as conn:
        r = S.apply_migrations(S._local_dsn() or "")
        assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
        conn.execute("DELETE FROM operation_runs")

    scope_id = "scope-silent-work"
    bump_calls: list[object] = []

    async def _run():
        db = await Database.connect(S._local_dsn())
        # The heartbeat side-effect under test: db.bump_activity(scope_id) must
        # fire on every heartbeat tick to keep the E2B sandbox alive during
        # silent (token-less) agent work. Wrap it with a delegating spy.
        assert hasattr(db, "bump_activity"), (
            "Database must expose bump_activity(scope_id) — the sandbox-keepalive seam"
        )
        orig_bump = db.bump_activity

        async def _spy(scope, *a, **k):
            bump_calls.append(scope)
            return await orig_bump(scope, *a, **k)

        db.bump_activity = _spy  # type: ignore[method-assign]
        try:
            # Silent work: the agent emits no tokens for longer than one
            # heartbeat interval, spanning at least two intervals.
            async with with_operation_run(
                db, scope_id, "meeting-harness", heartbeat_s=0.05
            ):
                await asyncio.sleep(0.22)  # > 2 heartbeat intervals, no token emission
        finally:
            await db.close()

    asyncio.run(_run())

    # Primary oracle (call_trace_spy): bump_activity fired on each heartbeat
    # tick, keyed to this run's scope_id — a run that advances last_heartbeat_at
    # but never bumps activity is non-conformant.
    assert len(bump_calls) >= 2, (
        f"db.bump_activity must be called on each heartbeat tick across the silent "
        f"window (>=2 intervals); got {len(bump_calls)} call(s)"
    )
    assert all(s == scope_id for s in bump_calls), (
        f"every bump_activity call must be keyed to the run's scope_id; got {bump_calls!r}"
    )

    # The heartbeat genuinely ran (last_heartbeat_at advanced) AND was coupled to
    # a bump — advancing the heartbeat without bumping activity is the fault.
    with S.pg_conn() as conn:
        row = conn.execute(
            "SELECT started_at, last_heartbeat_at FROM operation_runs "
            "WHERE scope_id=%s ORDER BY started_at DESC LIMIT 1",
            (scope_id,),
        ).fetchone()
        assert row is not None, "with_operation_run created no operation_runs row"
        assert row[1] > row[0], "last_heartbeat_at must advance during silent work"
        assert bump_calls, (
            "the heartbeat advanced but bump_activity was never called — the sandbox "
            "would time out during silent agent work (F-HEARTBEAT-NO-ACTIVITY-BUMP)"
        )
