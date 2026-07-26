"""Doc 04 · §3.16.1 — the accept-handler on the real durable path (integration).

A human accepts a staged draft via ``POST /m/{meeting_id}/drafts/{draft_id}/accept``
(CANONICAL §12.9) AFTER the call, when the sandbox review session is long dead. The
accept-handler MUST read DURABLE storage (the persisted ``staged_drafts`` row + its
GCS-versioned body), never the dead in-memory session, and:

  * notes-edit draft  → apply the edit into the notes object (a durable ``note_deltas``
                         append via Doc 03's write path) and flip the row to ``applied``;
  * replay same key   → return the FIRST result, never double-apply (idempotent);
  * cross-tenant      → denied (tenant derived server-side from draft→meeting→tenant,
                         never a client-supplied tenant);
  * bad CSRF          → denied;
  * code-change draft → record approval + expose the bundle link, NEVER push.

All bodies open ``S.pg_conn()`` (skips when no local Postgres), and import the product
FIRST so a missing product is RED, a missing DB is a skip. The apply runs on the real
tables migrated to head — no in-memory dict stands in for the durable read.
"""
from __future__ import annotations

import sys
import pathlib
import uuid

import pytest

pytestmark = [pytest.mark.integration]

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # noqa: E402  reuse pg_conn / apply_migrations / _local_dsn


def _require_schema(conn) -> None:
    """Ensure the substrate is at head for this test; skip if no reachable DB.

    ``build/setup-test-env.sh`` migrates the local DB to head before the suite. If
    the required tables are already present we run against them directly (rerunning
    alembic from a schema-present DB with an empty ``alembic_version`` would spuriously
    fail on a DuplicateTable). Only when a table is genuinely missing do we run the
    migrations to head.
    """
    for table in ("tenants", "meetings", "staged_drafts", "note_deltas"):
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
            r = S.apply_migrations(S._local_dsn() or "")
            assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
            return


def _seed_meeting(conn, *, tenant_name: str) -> tuple[str, str]:
    """Insert a tenant + a meeting under it; return (tenant_id, meeting_id)."""
    tid = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (tenant_name,)
    ).fetchone()[0]
    mid = conn.execute(
        "INSERT INTO meetings (tenant_id, status) VALUES (%s, 'ended') RETURNING id",
        (tid,),
    ).fetchone()[0]
    return str(tid), str(mid)


def _seed_draft(conn, *, meeting_id: str, kind: str, body: str) -> str:
    """Persist a GCS-versioned body + a 'proposed' staged_drafts row; return draft_id."""
    from workroom import objectstore

    artifact_ref = f"gs://proxy-drafts/{meeting_id}/{uuid.uuid4().hex}"
    objectstore.put(artifact_ref, body)
    did = conn.execute(
        """
        INSERT INTO staged_drafts (meeting_id, kind, summary, artifact_ref, status)
        VALUES (%s, %s, %s, %s, 'proposed')
        RETURNING draft_id
        """,
        (meeting_id, kind, f"{kind} summary", artifact_ref),
    ).fetchone()[0]
    return str(did)


def _request(*, tenant: str, csrf_valid: bool = True, user: str = "u@t"):
    """A minimal authed request object the handler reads its principal off of."""
    return type(
        "Req",
        (),
        {"authenticated": True, "tenant": tenant, "user": user, "csrf_valid": csrf_valid},
    )()


@pytest.mark.integration
def test_notes_edit_accept_applies_and_flips_to_applied():
    """Happy path: a core notes-edit draft applies the edit + flips row to 'applied'."""
    from control_plane.accept_route import handle_accept  # product first → red if absent

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="edited notes body")

        resp = handle_accept(
            conn,
            meeting_id=meeting,
            draft_id=draft,
            idempotency_key="k-1",
            request=_request(tenant=tenant),
        )
        assert resp.status == 200, resp
        assert resp.accepted is True
        assert resp.idempotent_replay is False

        # The row flipped to 'applied' in DURABLE storage (not 'accepted', not 'proposed').
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "applied", f"expected applied, got {status!r}"

        # The apply wrote the edit into the notes object (a durable note_deltas append).
        n = conn.execute(
            "SELECT count(*) FROM note_deltas WHERE meeting_id = %s", (meeting,)
        ).fetchone()[0]
        assert n >= 1, "notes-edit apply must append at least one durable note_delta"


@pytest.mark.integration
def test_replay_same_key_returns_first_result_no_double_apply():
    """Idempotent: replaying the same key returns the first result, never double-applies."""
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        first = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="same",
            request=_request(tenant=tenant),
        )
        deltas_after_first = conn.execute(
            "SELECT count(*) FROM note_deltas WHERE meeting_id = %s", (meeting,)
        ).fetchone()[0]

        replay = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="same",
            request=_request(tenant=tenant),
        )
        assert replay.status == 200
        assert replay.idempotent_replay is True, "replay must be flagged a replay"
        assert replay.accept_id == first.accept_id, "replay returns the FIRST accept id"

        # No double-apply: the delta count did not grow on replay.
        deltas_after_replay = conn.execute(
            "SELECT count(*) FROM note_deltas WHERE meeting_id = %s", (meeting,)
        ).fetchone()[0]
        assert deltas_after_replay == deltas_after_first, "replay must NOT re-apply"


@pytest.mark.integration
def test_cross_tenant_caller_is_denied():
    """Isolation: a member of a DIFFERENT tenant is refused (server-side draft→tenant)."""
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        _owner, meeting = _seed_meeting(conn, tenant_name=f"owner-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        resp = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k",
            request=_request(tenant=str(uuid.uuid4())),  # a DIFFERENT (attacker) tenant
        )
        assert resp.status == 403, resp
        assert resp.rejected is True

        # Nothing applied — the draft stays 'proposed' and no delta was written.
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "proposed"
        n = conn.execute(
            "SELECT count(*) FROM note_deltas WHERE meeting_id = %s", (meeting,)
        ).fetchone()[0]
        assert n == 0, "a denied cross-tenant accept must apply nothing"


@pytest.mark.integration
def test_bad_csrf_is_denied():
    """CSRF: an invalid CSRF token is rejected before any apply."""
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        resp = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k",
            request=_request(tenant=tenant, csrf_valid=False),
        )
        assert resp.status == 403, resp
        assert resp.rejected is True
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "proposed", "a bad-CSRF accept must apply nothing"


@pytest.mark.integration
def test_code_change_records_approval_and_exposes_bundle_without_pushing():
    """A code-change draft records approval + exposes the bundle link, NEVER pushes."""
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="code-change", body="the diff bundle")

        resp = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="cc-1",
            request=_request(tenant=tenant),
        )
        assert resp.status == 200, resp
        assert resp.accepted is True
        # Approval recorded (row flips to 'applied') + the bundle link is exposed.
        assert resp.bundle_url, "a code-change accept must expose the diff bundle link"
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "applied", f"code-change approval must be recorded, got {status!r}"

        # It must NOT push: no note_deltas written (code-change is not a notes apply)
        # and the handler never opened a PR/push (no push seam is invoked in core).
        assert resp.pushed is False, "a code-change accept must NEVER push (Expansion seam)"


@pytest.mark.integration
def test_accept_emits_an_audit_record_with_the_acting_tenant_member():
    """Audit is a HARD requirement (§2.8, CANONICAL §12.9): a world-touching accept is
    recorded with the acting tenant member. Pass an audit_sink and assert it captured
    exactly one record naming the accepting tenant + user + the applied draft.
    """
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        records: list[str] = []
        resp = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="audit-1",
            request=_request(tenant=tenant, user="alice@t"),
            audit_sink=records.append,
        )
        assert resp.status == 200 and resp.accepted is True
        assert len(records) == 1, f"a successful accept must emit exactly one audit record, got {records}"
        rec = str(records[0])
        assert "accept" in rec, "the audit record must name the action"
        assert str(tenant) in rec, "the audit record must name the acting tenant"
        assert "alice@t" in rec, "the audit record must name the acting user (member)"
        assert str(draft) in rec, "the audit record must name the applied draft"
        assert resp.accept_id and resp.accept_id in rec, "the audit record must carry the accept id"


@pytest.mark.integration
def test_accept_denials_and_replay_do_not_double_audit():
    """A refused accept (cross-tenant/CSRF) emits NO audit record, and a replay does
    NOT re-audit — audit fires once, only on the first world-touching apply.
    """
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        denied_records: list[str] = []
        denied = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k",
            request=_request(tenant=str(uuid.uuid4())),  # cross-tenant → refused
            audit_sink=denied_records.append,
        )
        assert denied.status == 403
        assert denied_records == [], "a refused accept must NOT be audited as an apply"

        records: list[str] = []
        first = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="same",
            request=_request(tenant=tenant), audit_sink=records.append,
        )
        replay = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="same",
            request=_request(tenant=tenant), audit_sink=records.append,
        )
        assert first.status == 200 and replay.idempotent_replay is True
        assert len(records) == 1, f"a replay must NOT re-audit; audit fires once, got {records}"


@pytest.mark.integration
def test_durable_idempotency_survives_lost_in_memory_ledger():
    """Post-restart safe: a replay after the in-memory ledger is gone still no-ops.

    The route's in-memory idempotency ledger does not survive a process recycle, so
    the DURABLE belt (the ``staged_drafts`` row already at ``status='applied'``) must
    still reject a re-apply — proving idempotency reads durable storage, not a live
    process dict. We simulate the recycle by clearing the module-level ledger.
    """
    from control_plane import accept_route
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k",
            request=_request(tenant=tenant),
        )
        deltas_after_first = conn.execute(
            "SELECT count(*) FROM note_deltas WHERE meeting_id = %s", (meeting,)
        ).fetchone()[0]

        # Simulate a process recycle: the in-memory ledger is gone, a DIFFERENT key.
        accept_route._ACCEPTS.clear()
        replay = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="fresh-key",
            request=_request(tenant=tenant),
        )
        assert replay.status == 200
        assert replay.applied_status == "applied"
        deltas_after_replay = conn.execute(
            "SELECT count(*) FROM note_deltas WHERE meeting_id = %s", (meeting,)
        ).fetchone()[0]
        assert deltas_after_replay == deltas_after_first, (
            "durable idempotency must prevent a double-apply after the ledger is lost"
        )
