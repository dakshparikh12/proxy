"""Doc 04 · §3.16.1 / CANONICAL §12.9 — the reject-handler on the real durable path.

Reject is the symmetric twin of accept (spec §2.8, CANONICAL §12.9): a human
declines a staged draft via ``POST /m/{meeting_id}/drafts/{draft_id}/reject`` AFTER
the call, when the sandbox review session is long dead. The reject-handler MUST read
DURABLE storage (the persisted ``staged_drafts`` row), never the dead in-memory
session, and:

  * any draft         → flip the row to ``status='rejected'`` and apply NOTHING (no
                         note_deltas append for a notes-edit, no push for a code-change);
  * replay same key   → return the FIRST result, never double-reject (idempotent);
  * cross-tenant      → denied (tenant derived server-side from draft→meeting→tenant,
                         never a client-supplied tenant);
  * bad CSRF          → denied;
  * code-change draft → declines it (NEVER pushes — reject is the opposite of a push).

All bodies open ``S.pg_conn()`` (skips when no local Postgres) and import the product
FIRST so a missing product is RED, a missing DB is a skip. The reject runs on the real
tables migrated to head — no in-memory dict stands in for the durable read/write.
"""
from __future__ import annotations

import pathlib
import sys
import uuid

import pytest

pytestmark = [pytest.mark.integration]

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # noqa: E402  reuse pg_conn / apply_migrations / _local_dsn


def _require_schema(conn) -> None:
    for table in ("tenants", "meetings", "staged_drafts", "note_deltas"):
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
            r = S.apply_migrations(S._local_dsn() or "")
            assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
            return


def _seed_meeting(conn, *, tenant_name: str) -> tuple[str, str]:
    tid = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (tenant_name,)
    ).fetchone()[0]
    mid = conn.execute(
        "INSERT INTO meetings (tenant_id, status) VALUES (%s, 'ended') RETURNING id",
        (tid,),
    ).fetchone()[0]
    return str(tid), str(mid)


def _seed_draft(conn, *, meeting_id: str, kind: str, body: str) -> str:
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
    return type(
        "Req",
        (),
        {"authenticated": True, "tenant": tenant, "user": user, "csrf_valid": csrf_valid},
    )()


@pytest.mark.integration
def test_reject_flips_row_to_rejected_and_applies_nothing():
    """Happy path: a reject flips the row to 'rejected' and writes NO note_deltas."""
    from control_plane.accept_route import handle_reject  # product first → red if absent

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="a body")

        resp = handle_reject(
            conn,
            meeting_id=meeting,
            draft_id=draft,
            idempotency_key="r-1",
            request=_request(tenant=tenant),
        )
        assert resp.status == 200, resp
        assert resp.rejected is True
        assert resp.accepted is False
        assert resp.idempotent_replay is False

        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "rejected", f"expected rejected, got {status!r}"

        # A reject applies NOTHING — no notes-edit delta was written.
        n = conn.execute(
            "SELECT count(*) FROM note_deltas WHERE meeting_id = %s", (meeting,)
        ).fetchone()[0]
        assert n == 0, "a reject must apply nothing (no note_delta append)"


@pytest.mark.integration
def test_reject_replay_same_key_returns_first_result_no_double():
    """Idempotent: replaying the same key returns the first result, never re-rejects."""
    from control_plane.accept_route import handle_reject

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        first = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="same-r",
            request=_request(tenant=tenant),
        )
        replay = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="same-r",
            request=_request(tenant=tenant),
        )
        assert replay.status == 200
        assert replay.idempotent_replay is True, "replay must be flagged a replay"
        assert replay.reject_id == first.reject_id, "replay returns the FIRST reject id"
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "rejected"


@pytest.mark.integration
def test_reject_cross_tenant_caller_is_denied():
    """Isolation: a member of a DIFFERENT tenant is refused (server-side draft→tenant)."""
    from control_plane.accept_route import handle_reject

    with S.pg_conn() as conn:
        _require_schema(conn)
        _owner, meeting = _seed_meeting(conn, tenant_name=f"owner-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        resp = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k",
            request=_request(tenant=str(uuid.uuid4())),  # a DIFFERENT (attacker) tenant
        )
        assert resp.status == 403, resp
        assert resp.rejected is True  # refused
        # Nothing changed — the draft stays 'proposed'.
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "proposed", "a denied cross-tenant reject must change nothing"


@pytest.mark.integration
def test_reject_bad_csrf_is_denied():
    """CSRF: an invalid CSRF token is rejected before any state change."""
    from control_plane.accept_route import handle_reject

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        resp = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k",
            request=_request(tenant=tenant, csrf_valid=False),
        )
        assert resp.status == 403, resp
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "proposed", "a bad-CSRF reject must change nothing"


@pytest.mark.integration
def test_reject_code_change_declines_without_pushing():
    """A code-change draft can be declined — reject NEVER pushes (opposite of a push)."""
    from control_plane.accept_route import handle_reject

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="code-change", body="the diff bundle")

        resp = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="cc-r",
            request=_request(tenant=tenant),
        )
        assert resp.status == 200, resp
        assert resp.rejected is True
        assert resp.pushed is False, "a code-change reject must NEVER push"
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "rejected"


@pytest.mark.integration
def test_reject_after_accept_is_a_noop_idempotent_belt():
    """A reject on an already-applied draft is a durable no-op, not a status flip-flop.

    The durable row-status belt (``status IN ('applied','rejected')``) short-circuits,
    so a draft a human already accepted cannot be silently un-applied by a later reject
    (§3.16.1) — the row stays 'applied' and the reject reports the durable state.
    """
    from control_plane.accept_route import handle_accept, handle_reject

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        acc = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="a",
            request=_request(tenant=tenant),
        )
        assert acc.status == 200 and acc.applied_status == "applied"

        rej = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="late-reject",
            request=_request(tenant=tenant),
        )
        # The durable belt: the row was already terminal, so the reject does not
        # flip an applied draft — the applied state is the durable witness.
        status = conn.execute(
            "SELECT status FROM staged_drafts WHERE draft_id = %s", (draft,)
        ).fetchone()[0]
        assert status == "applied", f"a reject must NOT un-apply an applied draft, got {status!r}"
        assert rej.applied_status == "applied"
