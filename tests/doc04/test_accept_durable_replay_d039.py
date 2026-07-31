"""D-039 regression — accept/reject replay is DURABLE + cross-instance stable.

The world-touching accept/reject click must replay IDENTICALLY on ANY control_plane instance
(multi-instance Cloud Run), not only the one that minted the response. Before D-039 the replay
body (accept_id, idempotent_replay) came from a process-local dict, so a retry on a second
instance returned a FRESH accept_id + idempotent_replay=false — a different body for the one
irreversible click. This proves the fix: after a simulated recycle (the in-memory ledger is
gone), a replay returns the SAME deterministic accept_id AND idempotent_replay=True, and never
double-applies. Reuses the sealed suite's durable-PG fixtures; a NEW file (no sealed edit).
"""
from __future__ import annotations

import uuid

import pytest

from tests.doc04.test_accept_handler_durable import (
    S,
    _request,
    _require_schema,
    _seed_draft,
    _seed_meeting,
)


@pytest.mark.integration
def test_accept_replay_is_cross_instance_stable_after_ledger_loss() -> None:
    from control_plane import accept_route
    from control_plane.accept_route import handle_accept

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        first = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k1",
            request=_request(tenant=tenant),
        )
        assert first.accepted and first.idempotent_replay is False

        # Simulate a recycle onto a fresh instance: the in-memory ledger is gone AND the
        # client retries under a DIFFERENT idempotency key. The durable staged_drafts belt +
        # the deterministic id must still replay the IDENTICAL response.
        accept_route._ACCEPTS.clear()
        replay = handle_accept(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k2-different-instance",
            request=_request(tenant=tenant),
        )
        assert replay.status == 200
        assert replay.idempotent_replay is True, "a cross-instance retry must be flagged a replay"
        assert replay.accept_id == first.accept_id, (
            "the replay must return the IDENTICAL accept id on any instance (D-039)"
        )


@pytest.mark.integration
def test_reject_replay_is_cross_instance_stable_after_ledger_loss() -> None:
    from control_plane import accept_route
    from control_plane.accept_route import handle_reject

    with S.pg_conn() as conn:
        _require_schema(conn)
        tenant, meeting = _seed_meeting(conn, tenant_name=f"t-{uuid.uuid4().hex[:8]}")
        draft = _seed_draft(conn, meeting_id=meeting, kind="notes-edit", body="body")

        first = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k1",
            request=_request(tenant=tenant),
        )
        assert first.rejected and first.idempotent_replay is False

        accept_route._REJECTS.clear()
        replay = handle_reject(
            conn, meeting_id=meeting, draft_id=draft, idempotency_key="k2-different-instance",
            request=_request(tenant=tenant),
        )
        assert replay.status == 200
        assert replay.idempotent_replay is True
        assert replay.reject_id == first.reject_id, (
            "the replay must return the IDENTICAL reject id on any instance (D-039)"
        )
