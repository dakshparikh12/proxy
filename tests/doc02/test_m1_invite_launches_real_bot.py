"""Doc 02 · M1 — the invite path launches a REAL Recall bot (AC-JOIN-01/10/15).

Regression for DOC02-RECALL-BOT-NEVER-LAUNCHED: ``harness.invite_proxy`` must NOT
fabricate a ``recall-bot-<uuid>`` id. It must drive the real ``TransportProvider``
(``RecallTransport`` bound to the funded ``call_external`` seam), write back the id
Recall actually returns, and post the consent notice pinned as the first observable
action — before observation begins.

All product imports live inside test bodies so collection stays clean.
"""
from __future__ import annotations

import asyncio
import re

import pytest

pytestmark = [pytest.mark.simulation, pytest.mark.integration]

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # reuse the pg harness helpers (pg_conn/apply_migrations/_local_dsn)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FundedRecallSeam:
    """A stand-in for ``libs.http.call_external`` funnel: runs the op closure (the
    sole raw round-trip), then substitutes the payload Recall's ``/bot`` API returns
    (a stable bot id). Records every service touched so we can prove the round-trip
    went through the seam (AC-XCUT-03) — not a fabricated id."""

    def __init__(self, bot_id: str | None = None) -> None:
        import uuid
        self.RECALL_BOT_ID = bot_id or f"recall-real-{uuid.uuid4().hex}"
        self.calls: list[dict] = []

    async def __call__(self, op, *, service, unit_cost_usd=0.0):
        result = await op()  # the real transport round-trip closure
        self.calls.append({"service": service, "unit_cost_usd": unit_cost_usd, "op_result": result})
        # /bot join → Recall returns the launched bot's id under "id".
        if isinstance(result, dict) and result.get("url", "").endswith("/bot"):
            return type("Outcome", (), {"value": {"id": self.RECALL_BOT_ID}})()
        return type("Outcome", (), {"value": result})()


@pytest.mark.integration
def test_invite_launches_real_bot_and_writes_back_recall_id():
    """AC-JOIN-01/10: invite drives RecallTransport.join, writes the REAL bot id back.

    criterion_id: AC-JOIN-10
    """
    from control_plane.meetings import invite_proxy, resolve_bot_id
    from libs.db import Database
    from transport.recall import RecallTransport

    r = S.apply_migrations(S._local_dsn() or "")
    assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
    with S.pg_conn() as conn:
        tid = conn.execute("INSERT INTO tenants (name) VALUES ('t') RETURNING id").fetchone()[0]
        rid = conn.execute(
            "INSERT INTO repos (tenant_id, full_name, default_branch) "
            "VALUES (%s, 'o/r', 'main') RETURNING id", (tid,)
        ).fetchone()[0]

    seam = _FundedRecallSeam()
    transport = RecallTransport(seam, api_key="sk-recall-test")

    async def _invite():
        db = await Database.connect(S._local_dsn())
        try:
            return await invite_proxy(
                db, tenant_id=tid, repo_id=rid,
                meeting_url="https://meet.google.com/abc-def-ghi",
                head_sha="deadbeef", transport=transport,
            )
        finally:
            await db.close()

    meeting = _run(_invite())

    # 1) The id written back is EXACTLY what Recall returned — never a fabricated uuid.
    assert meeting.recall_bot_id == seam.RECALL_BOT_ID
    assert not re.match(r"^recall-bot-[0-9a-f]{32}$", meeting.recall_bot_id), (
        "invite must not fabricate a synthetic recall-bot-<uuid> id"
    )

    # 2) The round-trip actually went through the funded call_external seam to Recall.
    services = [c["service"] for c in seam.calls]
    assert services.count("recall") >= 2, f"expected join + consent-post via seam; got {services}"

    # 3) The DB row carries the REAL launched bot id (AC-JOIN-10: no row without it).
    with S.pg_conn() as conn:
        row = conn.execute(
            "SELECT recall_bot_id FROM meetings WHERE id=%s", (meeting.id,)
        ).fetchone()
        assert row is not None and row[0] == seam.RECALL_BOT_ID

    # 4) The webhook resolver finds the meeting by the REAL id (AC-JOIN-11).
    async def _resolve():
        db = await Database.connect(S._local_dsn())
        try:
            return await resolve_bot_id(db, seam.RECALL_BOT_ID)
        finally:
            await db.close()

    resolved = _run(_resolve())
    assert resolved is not None
    assert str(resolved["tenant_id"]) == str(tid)
    assert str(resolved["repo_id"]) == str(rid)


@pytest.mark.integration
def test_invite_posts_pinned_consent_before_observation():
    """AC-JOIN-15/03: the consent notice is a REAL posted/pinned chat message.

    criterion_id: AC-JOIN-15
    """
    from control_plane.meetings import invite_proxy
    from libs.db import Database
    from transport.recall import RecallTransport
    from transport.consent import consent_notice

    r = S.apply_migrations(S._local_dsn() or "")
    assert r.returncode == 0
    with S.pg_conn() as conn:
        tid = conn.execute("INSERT INTO tenants (name) VALUES ('t') RETURNING id").fetchone()[0]
        rid = conn.execute(
            "INSERT INTO repos (tenant_id, full_name, default_branch) "
            "VALUES (%s, 'o/r', 'main') RETURNING id", (tid,)
        ).fetchone()[0]

    seam = _FundedRecallSeam()
    transport = RecallTransport(seam, api_key="sk-recall-test")

    async def _invite():
        db = await Database.connect(S._local_dsn())
        try:
            return await invite_proxy(
                db, tenant_id=tid, repo_id=rid,
                meeting_url="https://meet.google.com/abc-def-ghi",
                head_sha="deadbeef", transport=transport,
            )
        finally:
            await db.close()

    meeting = _run(_invite())

    # The consent notice really went out as a pinned chat message to the launched bot.
    chat_calls = [
        c for c in seam.calls
        if isinstance(c["op_result"], dict)
        and c["op_result"].get("url", "").endswith("/send_chat_message/")
    ]
    assert chat_calls, "consent notice must be posted via a real send_chat_message round-trip"
    body = chat_calls[0]["op_result"]["body"]
    assert body.get("message") == consent_notice()
    assert body.get("pin") is True, "consent line must be PINNED where the platform allows"

    # The join result reports notice_posted honestly and observation is gated on it.
    assert meeting.notice_posted is True
    assert meeting.recall_bot_id == seam.RECALL_BOT_ID
