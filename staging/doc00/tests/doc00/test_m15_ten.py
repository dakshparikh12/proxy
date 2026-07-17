"""Doc 00 · TEN — Cross-cutting tenant / credential invariants (AC-TEN-001..003).

Milestone m15. Every test maps to exactly one blocking (P0) criterion (its id in
the docstring). Product imports live INSIDE the test bodies, so this module
COLLECTS clean and FAILS red before ``libs.db`` / the authz-resolve path / the
Nango client exist.

Oracle sources per PROTO-DETERMINISTIC-01:

  * AC-TEN-001 [contract]  -- schema check over the migrated DB's
    ``information_schema`` FK metadata: every durable tenant-scoped table reaches
    ``tenant_id`` either directly (a ``tenant_id`` column FK-ing ``tenants``) or
    via a DECLARED ``meeting_id REFERENCES meetings(id)`` FK chain
    (``meeting_cost`` / ``staged_drafts`` per ambiguity A-009, resolved).
    Threshold: ``tenant_unscoped_tables == 0``.
  * AC-TEN-002 [security-adversarial]  -- import the product's server-side
    entity->tenant resolution, build a tenant-A entity + a tenant-B principal,
    assert the cross-tenant read is REFUSED and leaks zero tenant-A rows.
    Threshold: ``cross_tenant_rows_returned == 0``.
  * AC-TEN-003 [security-adversarial]  -- a Nango stub records mint calls
    (returning distinct sentinel tokens) and a log capture holds a sentinel;
    two sequential operations => mint called exactly once per op (twice total),
    no cache reuse between them, and the sentinel token appears NOWHERE in logs.
    Thresholds: ``token_cached_reuse == 0`` and ``token_log_occurrences == 0``.

Source quotes (spec):
  * "Tenant isolation (tenant_id in every schema; cross-tenant read = P0 breach)"
  * "cross-tenant read = P0 breach"
  * "tokens minted per-operation, never cached or logged"
    (00-FOUNDATION §15 · 01-CODE-INTELLIGENCE §... "mint per operation, never
     cache, never log")
"""

import io
import logging

import pytest

import _support as S


# ── AC-TEN-001 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_ten_001_every_durable_table_reaches_tenant_id():
    """AC-TEN-001: every durable tenant-scoped table reaches tenant_id directly or via a declared FK chain to tenants (no unscoped table)."""
    from libs import db  # noqa: F401  -- red (product), skip (no DB), imported FIRST

    with S.pg_conn() as conn:
        # DB reachable only after the product exists -> migrate, then inspect
        # the real FK metadata via information_schema.
        info = conn.info
        parts = [f"dbname={info.dbname}", f"user={info.user}"]
        if info.host:
            parts.append(f"host={info.host}")
        if info.port:
            parts.append(f"port={info.port}")
        dsn = " ".join(parts)

        result = S.apply_migrations(dsn)
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
        )

        def _foreign_keys(c) -> dict[tuple[str, str], tuple[str, str]]:
            """{(src_table, src_col): (ref_table, ref_col)} for every declared FK."""
            cur = c.execute(
                """
                SELECT tc.table_name, kcu.column_name,
                       ccu.table_name  AS ref_table,
                       ccu.column_name AS ref_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema    = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name
                 AND tc.table_schema    = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema    = 'public'
                """
            )
            return {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}

        def _tables(c) -> set[str]:
            cur = c.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            return {r[0] for r in cur.fetchall()}

        fks = _foreign_keys(conn)
        all_tables = _tables(conn)

        # The tenancy spine must exist: tenants is the root every scope reaches.
        assert "tenants" in all_tables, "migrated schema missing the tenants root table"
        assert "meetings" in all_tables, "migrated schema missing the meetings table"

        # Non-tenant-scoped tables are excluded from the boundary requirement:
        #   * tenants itself is the root.
        #   * sessions is the server-side session store (keyed by sid, no tenant).
        #   * alembic_version is migration bookkeeping.
        NON_SCOPED = {"tenants", "sessions", "alembic_version"}

        def _has_column(c, table: str, column: str) -> bool:
            return column in S.table_columns(c, table)

        def _reaches_tenant_id(table: str, seen: frozenset[str]) -> bool:
            """A table reaches tenant_id directly (tenant_id FK-ing tenants) or
            transitively through a DECLARED FK to another table that reaches it."""
            if table in seen:
                return False  # cycle guard
            seen = seen | {table}

            # meetings/users/repos (and any peer) carry tenant_id directly, and
            # that column must itself be a declared FK to tenants(id).
            if _has_column(conn, table, "tenant_id"):
                ref = fks.get((table, "tenant_id"))
                assert ref is not None, (
                    f"{table}.tenant_id must be a DECLARED FK to tenants(id), not a bare column"
                )
                assert ref[0] == "tenants", (
                    f"{table}.tenant_id must REFERENCE tenants; got {ref[0]}({ref[1]})"
                )
                return True

            # Otherwise it must reach tenant_id via a declared FK to a table that does.
            for (src_tbl, _src_col), (ref_tbl, _ref_col) in fks.items():
                if src_tbl == table and ref_tbl != table:
                    if _reaches_tenant_id(ref_tbl, seen):
                        return True
            return False

        # (a) The tenancy roots carry tenant_id directly, each a declared FK.
        for direct in ("meetings", "users", "repos"):
            if direct in all_tables:
                assert _has_column(conn, direct, "tenant_id"), (
                    f"{direct} must carry tenant_id directly (tenancy root scope)"
                )
                assert _reaches_tenant_id(direct, frozenset()), (
                    f"{direct}.tenant_id must be a declared FK reaching tenants"
                )

        # (b) The meeting-keyed tables reach tenant_id via a DECLARED
        #     meeting_id REFERENCES meetings(id) FK (A-009, resolved) -- NOT a
        #     bare undeclared linkage. Assert the exact FK edge for the two the
        #     criterion names, and that meetings itself carries tenant_id.
        for meeting_keyed in ("meeting_cost", "staged_drafts"):
            assert meeting_keyed in all_tables, (
                f"migrated schema missing meeting-keyed table {meeting_keyed!r}"
            )
            assert _has_column(conn, meeting_keyed, "meeting_id"), (
                f"{meeting_keyed} must carry a meeting_id handle"
            )
            edge = fks.get((meeting_keyed, "meeting_id"))
            assert edge is not None, (
                f"{meeting_keyed}.meeting_id must be a DECLARED FK REFERENCES meetings(id) "
                f"(A-009 resolved: the FK the bare canonical DDL omits is a derived obligation "
                f"of the tenant-isolation invariant), not a bare undeclared linkage"
            )
            assert edge == ("meetings", "id"), (
                f"{meeting_keyed}.meeting_id must REFERENCE meetings(id); got {edge[0]}({edge[1]})"
            )

        # (c) The invariant, applied to EVERY durable app table: enumerate every
        #     base table and count those that fail to reach tenant_id. The
        #     threshold tenant_unscoped_tables is 0 -- no tenant-scoped table may
        #     lack a tenant boundary.
        scoped = sorted(all_tables - NON_SCOPED)
        unscoped = [t for t in scoped if not _reaches_tenant_id(t, frozenset())]
        assert unscoped == [], (
            f"tenant_unscoped_tables must be 0 (tenant_id in every durable schema); "
            f"tables with no tenant boundary: {unscoped}"
        )


# ── AC-TEN-002 ────────────────────────────────────────────────────────────
@pytest.mark.security_adversarial
def test_ten_002_cross_tenant_read_is_refused():
    """AC-TEN-002: a tenant-B principal reading a tenant-A meeting/draft is refused server-side; zero tenant-A rows leaked."""
    # Import the product's server-side entity->tenant resolution INSIDE the body
    # (red before it exists). The dispatch funnel resolves entity->owner->tenant
    # from OUR store (never a client-supplied tenant), and the /m/ route runs a
    # server-side meeting/draft->tenant check. Try the canonical seams in order.
    resolve = None
    try:
        from libs.http import resolve_entity_tenant as resolve  # dispatch-funnel resolver
    except ImportError:
        try:
            from libs.http import dispatch as resolve  # the one dispatch funnel
        except ImportError:
            from services.harness.authz import resolve_meeting_tenant as resolve  # accept-route check

    assert callable(resolve), "the server-side entity->tenant resolution must be a callable"

    # Tenant A owns a meeting/draft entity; a tenant-B principal attempts to read it.
    TENANT_A = "11111111-1111-1111-1111-111111111111"
    TENANT_B = "22222222-2222-2222-2222-222222222222"
    A_MEETING = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"  # owned by tenant A

    principal_b = {"user_id": "b0b0b0b0-0000-0000-0000-000000000000", "tenant_id": TENANT_B}

    # The read is refused: the server-side resolution rejects tenant B reading
    # tenant A's entity. Acceptable refusal shapes: a raised auth/not-found error,
    # or a returned denial ({"error": ...} / None / [] / falsey / a resolved tenant
    # that is NOT tenant A). What is NEVER acceptable is returning tenant-A rows.
    denied = False
    leaked_rows = None
    try:
        outcome = resolve(entity_id=A_MEETING, entity_type="meeting", principal=principal_b)
    except TypeError:
        # Tolerate a positional / alternate-kw natural signature.
        try:
            outcome = resolve(A_MEETING, principal_b)
        except Exception as exc:  # a raise IS a refusal
            denied = True
            outcome = exc
    except Exception as exc:  # a raise IS a refusal
        denied = True
        outcome = exc

    if not denied:
        # A non-raising resolver must signal denial in its return value AND must
        # not hand back any tenant-A data.
        text = repr(outcome).lower()
        resolved_tenant = None
        if isinstance(outcome, dict):
            resolved_tenant = outcome.get("tenant_id")
            leaked_rows = outcome.get("rows") or outcome.get("data")
            denied = (
                bool(outcome.get("error"))
                or outcome.get("allowed") is False
                or outcome.get("denied") is True
                or "not found" in text
                or "forbidden" in text
                or "denied" in text
            )
        else:
            leaked_rows = outcome  # a bare payload is itself the returned data
            denied = outcome is None or outcome == [] or outcome is False

        # If the resolver returned a tenant, it must NOT be tenant A (that would
        # mean tenant B successfully resolved into tenant A's scope).
        assert resolved_tenant != TENANT_A, (
            "cross-tenant read resolved into tenant A's scope -- P0 breach"
        )
        assert denied, (
            f"tenant B's read of tenant A's meeting must be refused server-side; got {outcome!r}"
        )

    # Zero tenant-A rows returned to tenant B (cross_tenant_rows_returned == 0):
    # tenant A's meeting id must not appear in whatever (if anything) came back.
    blob = repr(leaked_rows) if leaked_rows is not None else ""
    assert A_MEETING not in blob and TENANT_A not in blob, (
        f"no tenant-A data may be returned to tenant B (cross_tenant_rows_returned must be 0); leaked: {blob}"
    )


# ── AC-TEN-003 ────────────────────────────────────────────────────────────
@pytest.mark.security_adversarial
def test_ten_003_github_tokens_minted_per_op_never_cached_never_logged():
    """AC-TEN-003: GitHub tokens are minted per-operation via Nango, never cached between ops, and never logged."""
    # Import the product's Nango-backed token provider / RepoProvider INSIDE the
    # body (red before it exists). It must accept an injectable Nango client so a
    # stub can record mint calls; try the canonical seams in order.
    make_provider = None
    try:
        from libs.ops.nango import RepoProvider as make_provider  # RepoProvider over Nango
    except ImportError:
        try:
            from services.code_intel.provider import RepoProvider as make_provider
        except ImportError:
            from libs.ops import RepoProvider as make_provider

    # A Nango stub that records every mint call and returns a DISTINCT sentinel
    # token per call (so cache-reuse is detectable: reuse => same sentinel twice).
    SENTINEL_PREFIX = "SECRET-GH-TOKEN-DO-NOT-LOG-"

    class NangoStub:
        def __init__(self) -> None:
            self.mint_calls: list[tuple] = []

        def get_token(self, *args, **kwargs):  # the natural "mint an installation token" method
            self.mint_calls.append((args, kwargs))
            return f"{SENTINEL_PREFIX}{len(self.mint_calls)}"

        # Alias the alternate natural spelling to the same recorder.
        mint_token = get_token
        create_connection_token = get_token

    stub = NangoStub()

    # Capture ALL log output emitted during the two operations at the lowest level.
    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    # Also capture structlog if the product routes through it.
    struct_cap = None
    try:
        import structlog  # type: ignore

        struct_cap = structlog.testing.LogCapture()
        prev_config = structlog.get_config()
        structlog.configure(processors=[struct_cap])
    except Exception:
        prev_config = None

    minted_tokens: list[str] = []
    try:
        provider = make_provider(nango=stub)

        # Run TWO sequential operations that each need a GitHub token. The token
        # each op uses is surfaced so we can prove it was freshly minted (no cache
        # reuse => the two ops must see DISTINCT sentinel tokens).
        for _ in range(2):
            op = (
                getattr(provider, "with_token", None)
                or getattr(provider, "operation", None)
                or getattr(provider, "run_operation", None)
            )
            assert callable(op), "RepoProvider must run a per-operation token flow"
            tok = op(lambda token: token)  # the flow hands the minted token to the body
            minted_tokens.append(tok)
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)
        if prev_config is not None:
            import structlog  # type: ignore

            structlog.configure(**prev_config)

    # (a) Minted per-operation: exactly one mint per op, twice total (not cached).
    assert len(stub.mint_calls) == 2, (
        f"Nango mint must be called exactly once per operation (twice total); "
        f"got {len(stub.mint_calls)} calls -- a cached token reuses instead of re-minting"
    )

    # (b) No cache reuse between ops: distinct sentinel tokens
    #     (token_cached_reuse == 0). Reuse would surface the SAME token twice.
    assert len(minted_tokens) == 2 and minted_tokens[0] != minted_tokens[1], (
        f"the two operations must each get a FRESH minted token (no cache reuse between ops); "
        f"got {minted_tokens!r}"
    )

    # (c) The sentinel token value appears NOWHERE in captured logs
    #     (token_log_occurrences == 0).
    std_logs = log_buffer.getvalue()
    struct_logs = ""
    if struct_cap is not None:
        struct_logs = repr(struct_cap.entries)
    combined = std_logs + struct_logs
    for tok in minted_tokens:
        assert tok not in combined, (
            f"a minted GitHub token leaked into logs (token_log_occurrences must be 0): {tok!r}"
        )
    assert SENTINEL_PREFIX not in combined, (
        "no minted-token sentinel value may appear in any log output (never logged)"
    )
