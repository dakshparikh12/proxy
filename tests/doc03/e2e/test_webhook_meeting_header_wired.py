"""AC-SCRIBE-HEADER-WIRED — the in_call webhook builds the meeting's Scribe header.

Gap DOC03-MEETING-HEADER-EMPTY-IN-PRODUCTION: on the SOLE production meeting-join path
(``control_plane.webhooks._dispatch_meeting_event`` ``is_start`` branch) the Scribe was started
with ``MeetingHeader(meeting_id=meeting_id)`` — empty ``agenda``/``participants``/
``glossary``. §3.2 specifies the cached Scribe prefix carries "meeting header +
participants + glossary — set once at join", and ``render_header`` renders participants in
a stable sort as part of the byte-stable cached head (Segment A, AC-SCRIBE-04). In
production that head was empty, so the Scribe had no roster/agenda grounding in its cached
prefix — name-aware attribution and domain context degraded to whatever the transcript
window alone supplied.

The real, grounded source is the SAME Recall webhook envelope the drain already processes:
``transport.events.meeting_metadata`` reads ``data.title`` (agenda) + ``data.participants``
(each ``{name}``) verbatim from the callback (AC-EVENTS-05, "traceable to a field in the
source payload, never synthesized"). This test drives the REAL webhook join path (NO manual
header injection): it seeds a meeting, ingests a real ``bot.in_call`` webhook whose ``data``
carries a title + participant roster, drains it through ``drain_pending_webhooks`` (the ONE
production caller of ``start_meeting``), and asserts the STARTED runtime's frozen header
carries those participants + agenda — and that ``render_header`` renders them stable-sorted
into the byte-stable Segment A the Scribe caches. Before the fix the header is empty.
"""
from __future__ import annotations

import os
import uuid

import pytest

from db import Database, open_pool, repos
from scribe.prefix import build_scribe_prefix

from control_plane.meeting_runtime import MeetingRuntimeRegistry
from control_plane.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN,
    reason="integration tier: no TEST_DATABASE_URL (root conftest auto-provisions :55432)",
)


@requires_pg
@pytest.mark.asyncio
async def test_in_call_webhook_populates_scribe_header_from_recall_metadata() -> None:
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")
    registry = MeetingRuntimeRegistry(db)

    tenant_name = f"t-{uuid.uuid4().hex[:8]}"
    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id", tenant_name
        )

    bot_id = f"recall-bot-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        meeting = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant["id"],
            repo_id=None,
            meeting_url="https://meet.example/planning",
            pinned_sha="deadbeef",
            recall_bot_id=bot_id,
            status="live",
        )
    meeting_id = str(meeting["id"])

    # A real Recall in_call webhook: its data envelope carries the meeting title + the
    # already-present roster (the exact shape transport.events.meeting_metadata reads).
    webhook = {
        "event": "bot.in_call",
        "data": {
            "bot_id": bot_id,
            "title": "Q3 Planning",
            "participants": [
                {"id": "p2", "name": "Zara"},
                {"id": "p1", "name": "Ana"},
                {"id": "p3", "name": "Ben"},
            ],
        },
    }
    guid = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(conn, guid, webhook)
    assert await drain_pending_webhooks(db, registry=registry) >= 1

    runtime = registry.get(meeting_id)
    assert runtime is not None, "in_call did not START a MeetingRuntime"

    # THE GAP: the started runtime's frozen header must carry the roster + agenda from the
    # webhook metadata. Before the fix agenda="" and participants=() (empty cached head).
    header = runtime.header
    assert set(header.participants) == {"Ana", "Ben", "Zara"}, (
        f"header participants not populated from Recall roster: {header.participants!r}"
    )
    assert header.agenda == "Q3 Planning", (
        f"header agenda not populated from Recall title: {header.agenda!r}"
    )

    # And it renders stable-sorted into the byte-stable Segment A the Scribe caches (§3.2 /
    # AC-SCRIBE-04): participants alphabetized, agenda present — not the empty "(none)" head.
    rendered = header.render_header()
    assert "Participants: Ana, Ben, Zara" in rendered, rendered
    assert "Agenda: Q3 Planning" in rendered, rendered

    # The cached prefix Segment A carries that rendered head (the real bytes the Scribe caches).
    blocks = build_scribe_prefix(header, "")
    assert "Participants: Ana, Ben, Zara" in blocks[0]["text"]
    assert "Agenda: Q3 Planning" in blocks[0]["text"]

    await registry.end_meeting(meeting_id)
