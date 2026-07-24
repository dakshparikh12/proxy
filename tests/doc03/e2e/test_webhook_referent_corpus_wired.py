"""AC-REFM-CORPUS-WIRED — the in_call webhook builds the meeting's referent corpus.

Gap DOC03-REFERENT-CORPUS-UNWIRED-IN-PRODUCTION: on the SOLE production meeting-join
path (``harness.webhooks._dispatch_meeting_event`` ``is_start`` branch) the Scribe was
started with ``registry.start_meeting(header, carrier)`` and NO ``referent_corpus``. The
binding logic (``_bind_referents`` -> ``bind_referent`` over ``graph_nodes``) is correct
and threads end-to-end from ``start_meeting`` -> ``MeetingRuntime`` -> ``build_real_seams``
-> the applier, but with ``corpus is None`` EVERY referent was labelled
``binding_status='unbound'`` with ``binding=None``. That unbound map rides the note payload
read verbatim by the Workroom over ``GET /internal/notes`` (``read_notes``), so in
production the Workroom started every task with NO code orientation — contradicting §3.4
("lets the Workroom start a task already knowing which part of the codebase the room
means") and the §1.1/§3.11 ``checkout -> payments/checkout`` headline.

This test drives the REAL webhook join path (no manual corpus injection): it seeds a repo
whose real per-tenant ``graph.db`` (the exact artifact ``code_intel.graph_store`` writes)
carries a ``checkout`` node, ingests a real ``bot.in_call`` webhook, drains it through
``drain_pending_webhooks`` (the ONE production caller of ``start_meeting``), and asserts the
STARTED runtime carries a real ``ReferentCorpus`` resolved from ``repo_id`` — pointed at the
real on-disk graph.db. Then, folding a Claim carrying ``checkout`` through the SAME
production seams the runtime built (``build_real_seams(header, db, referent_corpus=
runtime.referent_corpus)``) and reading it back with the production ``read_notes`` fold
(the exact bytes ``/internal/notes`` serves), the entry binds to the REAL node id from the
corpus. Before the fix ``runtime.referent_corpus is None`` and the binding is ``unbound``.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from code_intel.paths import repo_name_from_url, tenant_repo_dir
from db import Database, open_pool, repos
from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.notes_reader import Notes, read_notes
from scribe.prefix import MeetingHeader
from scribe.schema import AddOp, Claim, Firmness, NoteDelta, Provenance

from harness.meeting_runtime import MeetingRuntimeRegistry
from harness.scribe_runtime import build_real_seams
from harness.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN,
    reason="integration tier: no TEST_DATABASE_URL (root conftest auto-provisions :55432)",
)

# The 3.11 worked example: an area "payments/checkout" + a file "payments/checkout.py"
# exporting the "checkout" symbol — the same row shape code_intel.graph_store writes.
_SEED_ROWS = [
    ("payments/checkout.py::checkout", "payments/checkout", "payments/checkout.py", "checkout"),
]


def _seed_graph_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE graph_nodes (node_id TEXT PRIMARY KEY, area TEXT, file TEXT, symbol TEXT)"
        )
        conn.executemany(
            "INSERT INTO graph_nodes (node_id, area, file, symbol) VALUES (?, ?, ?, ?)",
            _SEED_ROWS,
        )
        conn.commit()
    finally:
        conn.close()


def _window(start: float, end: float) -> Window:
    seg = TranscriptSegment(speaker="Ana", text="w", start_s=start, end_s=end, token_count=1)
    return Window(segments=(seg,), boundary_type=BoundaryType.STREAM_END)


@requires_pg
@pytest.mark.asyncio
async def test_in_call_webhook_resolves_referent_corpus_from_repo() -> None:
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")
    registry = MeetingRuntimeRegistry(db)

    tenant_name = f"t-{uuid.uuid4().hex[:8]}"
    # A distinct repo name so the resolved per-tenant graph.db path is unique to this test.
    full_name = f"acme/checkout-{uuid.uuid4().hex[:8]}"

    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id", tenant_name
        )
        repo = await conn.fetchrow(
            "INSERT INTO repos (tenant_id, full_name, default_branch) VALUES ($1,$2,$3) RETURNING id",
            tenant["id"], full_name, "main",
        )

    # Materialise the repo's real per-tenant graph.db at the exact path production resolves.
    repo_name = repo_name_from_url(full_name)
    graph_db = tenant_repo_dir(str(tenant["id"]), repo_name) / "graph.db"
    _seed_graph_db(graph_db)

    bot_id = f"recall-bot-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        meeting = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant["id"],
            repo_id=repo["id"],
            meeting_url="https://meet.example/checkout",
            pinned_sha="deadbeef",
            recall_bot_id=bot_id,
            status="live",
        )
    meeting_id = str(meeting["id"])

    # Ingest + drain a real in_call webhook — the ONE production caller of start_meeting.
    guid = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(conn, guid, {"event": "bot.in_call", "data": {"bot_id": bot_id}})
    assert await drain_pending_webhooks(db, registry=registry) >= 1

    runtime = registry.get(meeting_id)
    assert runtime is not None, "in_call did not START a MeetingRuntime"

    # THE GAP: the started runtime must carry a real corpus resolved from repo_id, pointed
    # at the real on-disk graph.db. Before the fix this is None (every referent unbound).
    corpus = runtime.referent_corpus
    assert corpus is not None, (
        "in_call started the Scribe with NO referent corpus — the Workroom gets zero code "
        "orientation (DOC03-REFERENT-CORPUS-UNWIRED-IN-PRODUCTION)"
    )
    assert corpus.db_path is not None and Path(corpus.db_path) == graph_db, (
        f"corpus db_path {corpus.db_path!r} is not the repo's real graph.db {graph_db!r}"
    )

    # Prove the resolved corpus actually binds through the SAME production seams the runtime
    # built, read back with the production read_notes fold (the /internal/notes bytes).
    header = MeetingHeader(meeting_id=meeting_id)
    seams = build_real_seams(header, db, referent_corpus=corpus)
    await seams.apply_delta(
        meeting_id,
        _window(0.0, 5.0),
        NoteDelta(
            ops=[
                AddOp(
                    entry=Claim(
                        text="checkout is slow at peak",
                        speaker="Ana",
                        said_at_s=1.0,
                        firmness=Firmness.firm,
                        provenance=Provenance.observed,
                        referents=["checkout"],
                    )
                )
            ]
        ),
    )

    notes: Notes = await read_notes(meeting_id, db=db)
    entry = notes.entries[notes.order[0]]
    bindings = entry["referent_bindings"]
    bound = bindings["checkout"]
    assert bound["binding_status"] == "bound", bound
    assert bound["binding"] == "payments/checkout.py::checkout", bound

    await registry.end_meeting(meeting_id)
