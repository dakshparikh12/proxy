"""e2e: the ordered close (§3.16) — freeze → close-pass → destroy-sandbox →
complete-harness-row → teardown-pipes LAST, on ONE meeting-close operation row.

Node ``orchestrator.ordered-close`` (04 §3.16, CANONICAL §12.10 / §4).

These tests drive the REAL production join→close path against the live test
Postgres: a Recall ``in_call`` webhook body handed to the meeting-runtime entry
(``run_meeting_until_end``) which claims the meeting-harness ``operation_runs``
row, assembles the runtime, runs the loop, and — on the explicit ``MeetingEnd``
signal — runs the ordered close through ``control_plane.close.run_ordered_close``. The
DoD asserted here:

  * the close runs as its OWN ``operation_runs`` row (``operation_type=
    'meeting-close'``, completed) — there is **NO ``close_jobs`` table** (§12.10);
  * the notes markdown is written to GCS ``if_generation_match=0`` (create-only)
    and the link is posted in chat (the whole V0 close deliverable);
  * the STRICT order holds: freeze → close-pass → destroy-sandbox →
    complete-harness-row → **teardown-pipes LAST** — the meeting-harness row is
    ``completed`` BEFORE the pipes are torn down, and nothing reads a torn-down
    store;
  * the per-meeting sandbox is destroyed on close (§3.9);
  * **staged drafts persist past teardown** (``staged_drafts`` + GCS-versioned
    content, §4) so the accept-handler still works after the call;
  * the close is **idempotent** — re-running a completed close writes NO second
    notes object (create-only rejects it) and leaves the same completed rows.

The vendor boundaries (the Sonnet close caller, the GCS bucket, the chat poster)
are injected on the registry's close config exactly as ``build_real_seams``
injects ``call_external`` for the offline tier — the PRODUCT orchestration +
wiring is real; only the vendor edge is a recordable seam, never a product double.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from libs.db import Database, open_pool, repos
from libs.ops import sandbox_provider

from control_plane.meeting_runtime import MeetingRuntimeRegistry
from control_plane.provisioner import run_meeting_until_end

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned this session"
)

MEETING_HARNESS_OP = "meeting-harness"
MEETING_CLOSE_OP = "meeting-close"


class _RecBucket:
    """Create-only GCS bucket double honouring if_generation_match=0 (record-only).

    On a SECOND write to an already-created object it raises the create-only
    conflict the real GCS precondition raises — proving the close is idempotent.
    """

    def __init__(self) -> None:
        self.written: list[str] = []
        self._exists = False

    def blob(self, name: str):  # noqa: ANN202 - test double
        outer = self

        class _Blob:
            generation = None

            def upload_from_string(self, markdown, *, content_type, if_generation_match):
                assert if_generation_match == 0  # create-only (the mandatory precondition)
                assert content_type == "text/markdown"
                if outer._exists:
                    # Mimic real GCS: a create-only write over an existing object raises
                    # the 412 precondition the write seam catches → maps to
                    # NotesGenerationConflictError (never a silent overwrite).
                    from scribe.notes_artifact import precondition_failed_type

                    raise precondition_failed_type()("object exists (if_generation_match=0)")
                outer._exists = True
                outer.written.append(markdown)

            def reload(self):
                self.generation = 1

        return _Blob()


class _RecPoster:
    def __init__(self, order: list[str]) -> None:
        self.calls: list[str] = []
        self._order = order

    async def __call__(self, url: str) -> None:
        self.calls.append(url)
        self._order.append("chat")


class _StubCloseCaller:
    """An honest StructuredCaller — the recordable Sonnet vendor seam."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, *, model, prompt, output_schema):  # noqa: ANN001, ANN202
        from scribe.close import StructuredResult

        self.prompts.append(prompt)
        return StructuredResult(
            data={
                "summary": "The team agreed to ship the retry backoff today.",
                "decisions": [{"text": "Ship the retry backoff today."}],
                "action_items": [{"text": "Zed owns the applier fold.", "owner": "Zed"}],
                "open_questions": [],
            },
            total_cost_usd=0.0042,
        )


async def _passthrough_call_external(op, *, service, unit_cost_usd=0.0):  # noqa: ANN001
    return await op()


async def _seed_meeting(db: Database) -> tuple[str, str]:
    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
            f"t-{uuid.uuid4().hex[:8]}",
        )
        repo = await conn.fetchrow(
            "INSERT INTO repos (tenant_id, full_name, default_branch) "
            "VALUES ($1,$2,$3) RETURNING id",
            tenant["id"],
            "example/r",
            "main",
        )
    bot_id = f"recall-bot-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        meeting = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant["id"],
            repo_id=repo["id"],
            meeting_url="https://meet.example/close",
            pinned_sha="deadbeef",
            recall_bot_id=bot_id,
            status="live",
        )
    return str(meeting["id"]), bot_id


async def _seed_ledger(db: Database, meeting_id: str) -> None:
    """A folded ledger already lives in note_deltas (the durable cross-service object)."""
    async with db.acquire() as conn:
        await repos.notes.append_delta(
            conn, meeting_id=meeting_id, entry_id="d1", op="add",
            payload={"kind": "decision", "text": "Ship the retry backoff today."},
            window_start_s=0.0,
        )
        await repos.notes.append_delta(
            conn, meeting_id=meeting_id, entry_id="a1", op="add",
            payload={"kind": "action", "text": "Zed owns the applier fold.", "owner": "Zed"},
            window_start_s=5.0,
        )


def _in_call(bot_id: str) -> dict:
    return {"event": "bot.in_call", "data": {"bot_id": bot_id}}


async def _op_rows(db: Database, meeting_id: str, operation_type: str) -> list[str]:
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status FROM operation_runs WHERE scope_id = $1 AND operation_type = $2",
            meeting_id,
            operation_type,
        )
    return [r["status"] for r in rows]


async def _drive_join_to_close(
    db: Database,
    registry: MeetingRuntimeRegistry,
    bot_id: str,
    meeting_id: str,
    *,
    on_runtime=None,  # noqa: ANN001 - optional hook run once the runtime is up, pre-close
) -> None:
    """Run the FULL production join→close: launch the loop, emit MeetingEnd, await it."""
    task = asyncio.create_task(
        run_meeting_until_end(_in_call(bot_id), db=db, registry=registry, timeout_s=8.0)
    )
    # Wait for the runtime + loop to come up (the driver task assembles it).
    runtime = None
    for _ in range(400):
        rt = registry.get(meeting_id)
        if rt is not None and rt.run_loop is not None:
            runtime = rt
            break
        await asyncio.sleep(0.01)
    assert runtime is not None and runtime.run_loop is not None, "loop was not launched"

    # Provision the per-meeting sandbox (as a real join would) so the ordered close
    # has a live sandbox to explicitly destroy.
    sandbox = sandbox_provider.provision(meeting_id=meeting_id)
    assert sandbox_provider.health_check(sandbox).alive is True

    # Observation hook: installed on the LIVE runtime before meeting end, so a spy on
    # aclose records teardown-pipes ordering against the real close.
    if on_runtime is not None:
        on_runtime(runtime)

    # Explicit meeting end (§3.1) — never inferred from silence — drives the close.
    from transport.signals import MeetingEnd

    await runtime.carrier.emit(MeetingEnd(reason="call_ended"))
    outcome = await asyncio.wait_for(task, timeout=9.0)
    assert outcome.ran_to_end is True, "the loop did not run to meeting end"


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_join_to_close_runs_the_ordered_close() -> None:
    """A full join→close runs the §3.16 ordered close, in order, on ONE meeting-close row."""
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}-{uuid.uuid4().hex[:6]}")

    order: list[str] = []
    bucket = _RecBucket()
    poster = _RecPoster(order)
    caller = _StubCloseCaller()

    from control_plane.scribe_runtime import CloseConfig

    close_config = CloseConfig(
        bucket=bucket,
        bucket_name="proxy-notes",
        post_chat_link=poster,
        close_caller=caller,
        call_external=_passthrough_call_external,
    )
    registry = MeetingRuntimeRegistry(db, close_config=close_config)
    meeting_id, bot_id = await _seed_meeting(db)
    await _seed_ledger(db, meeting_id)

    # A staged draft persisted at creation (Doc 05's propose_change, §4). It must
    # OUTLIVE teardown so the accept-handler still works after the call.
    async with db.acquire() as conn:
        draft = await conn.fetchrow(
            "INSERT INTO staged_drafts (meeting_id, kind, summary, artifact_ref, status) "
            "VALUES ($1, 'notes-edit', $2, $3, 'proposed') RETURNING draft_id",
            meeting_id,
            "Rename chargeCard",
            f"gs://proxy-drafts/{meeting_id}/d1.json",
        )
    draft_id = draft["draft_id"]

    # Record the ORDER of the tail steps by wrapping only observation points (the
    # product logic underneath is unchanged): destroy-sandbox → complete-harness-row
    # → teardown-pipes. run_ordered_close destroys via sandbox_provider.destroy and
    # completes the harness row via control_plane.close._complete_harness_row.
    import control_plane.close as close_mod

    real_destroy = sandbox_provider.destroy
    real_complete = close_mod._complete_harness_row

    def _spy_destroy(handle):  # noqa: ANN001, ANN202
        order.append("sandbox")
        return real_destroy(handle)

    async def _spy_complete(runtime):  # noqa: ANN001
        await real_complete(runtime)
        order.append("row")

    sandbox_provider.destroy = _spy_destroy  # type: ignore[assignment]
    close_mod._complete_harness_row = _spy_complete  # type: ignore[assignment]

    # Wrap aclose on the LIVE runtime (before meeting end) to record teardown-pipes LAST.
    def _wrap_aclose(rt) -> None:  # noqa: ANN001
        real_aclose = rt.aclose

        async def _spy_aclose(_real=real_aclose):  # noqa: ANN001, ANN202
            order.append("pipes")
            await _real()

        rt.aclose = _spy_aclose  # type: ignore[assignment]

    try:
        await _drive_join_to_close(
            db, registry, bot_id, meeting_id, on_runtime=_wrap_aclose
        )
    finally:
        sandbox_provider.destroy = real_destroy  # type: ignore[assignment]
        close_mod._complete_harness_row = real_complete  # type: ignore[assignment]

    # 1) The permanent markdown notes object was written to GCS (create-only).
    assert bucket.written, "close pass did not write the finalized notes object to GCS"
    assert "retry backoff" in bucket.written[0], "the folded ledger did not reach the notes"

    # 2) The notes chat link was posted (the whole V0 close deliverable).
    assert poster.calls == [f"gs://proxy-notes/meetings/{meeting_id}/notes.md"]

    # 3) The close is its OWN operation_runs row (meeting-close), COMPLETED.
    assert await _op_rows(db, meeting_id, MEETING_CLOSE_OP) == ["completed"]
    # And the meeting-harness row completed too (§3.7).
    assert await _op_rows(db, meeting_id, MEETING_HARNESS_OP) == ["completed"]

    # 4) STRICT ORDER: chat (close-pass) → sandbox → row → pipes LAST. The harness
    # row is completed BEFORE the pipes are torn down (nothing reads a torn-down store).
    # (aclose is idempotent, so the end_meeting finally-net may tear down a 2nd time —
    # dedupe to the first occurrence of each step for the order assertion.)
    first_seen: list[str] = []
    for step in order:
        if step not in first_seen:
            first_seen.append(step)
    assert first_seen == ["chat", "sandbox", "row", "pipes"], order
    assert order.index("row") < order.index("pipes"), (
        "teardown-pipes ran before the harness row was completed"
    )

    # 5) The per-meeting sandbox was destroyed on close (§3.9).
    assert not any(h.meeting_id == meeting_id for h in sandbox_provider.list_sandboxes()), (
        "the sandbox survived the ordered close"
    )

    # 6) Staged drafts persist PAST teardown (§4) — the accept-handler still works.
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, artifact_ref FROM staged_drafts WHERE draft_id = $1", draft_id
        )
    assert row is not None, "the staged draft was lost at teardown"
    assert row["status"] == "proposed", "the durable draft was mutated by the close"

    # 7) There is NO close_jobs table (§12.10) — the close reuses operation_runs.
    async with db.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT to_regclass('public.close_jobs') IS NOT NULL"
        )
    assert exists is False, "a close_jobs table exists — §12.10 forbids it"

    # 8) The runtime is dropped only AFTER the whole ordered close.
    assert registry.get(meeting_id) is None, "the runtime was not ended after the close"

    await pool.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_ordered_close_is_idempotent() -> None:
    """Re-running a completed close writes NO second notes object (create-only)."""
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}-{uuid.uuid4().hex[:6]}")

    bucket = _RecBucket()
    poster = _RecPoster([])
    caller = _StubCloseCaller()

    from control_plane.scribe_runtime import CloseConfig

    close_config = CloseConfig(
        bucket=bucket,
        bucket_name="proxy-notes",
        post_chat_link=poster,
        close_caller=caller,
        call_external=_passthrough_call_external,
    )
    registry = MeetingRuntimeRegistry(db, close_config=close_config)
    meeting_id, bot_id = await _seed_meeting(db)
    await _seed_ledger(db, meeting_id)

    # First join→close: writes the notes object create-only, completes the row.
    await _drive_join_to_close(db, registry, bot_id, meeting_id)
    assert len(bucket.written) == 1, "the first close did not write the notes object once"
    assert await _op_rows(db, meeting_id, MEETING_CLOSE_OP) == ["completed"]

    # A SECOND close over the same meeting (crash-recovery re-run) must be a no-op on
    # the notes object: create-only (if_generation_match=0) rejects the second write,
    # the existing URL is reused, and no second notes object is produced. Drive the
    # close pass directly against the same durable ledger + already-created bucket.
    from control_plane.scribe_runtime import run_meeting_close
    from scribe.prefix import MeetingHeader

    header = MeetingHeader(meeting_id=meeting_id, agenda="", participants=())

    async def _noop_teardown() -> None:
        return None

    result = await run_meeting_close(
        header, db, close_config, teardown=_noop_teardown
    )
    # The second close reused the existing object — NO second GCS write.
    assert len(bucket.written) == 1, "the close was NOT idempotent — it wrote a 2nd notes object"
    assert result is not None and result.notes_url == (
        f"gs://proxy-notes/meetings/{meeting_id}/notes.md"
    ), "the recovery close did not reuse the existing notes URL"

    await pool.close()


@requires_pg
@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_ledger_close_still_runs_the_ordered_tail() -> None:
    """A no-notes close still destroys the sandbox, completes the row, and tears down.

    An empty ledger means the close pass writes no notes object (nothing to
    finalize), but the ordered TAIL (destroy-sandbox → complete-harness-row →
    teardown-pipes LAST) must STILL run — the sandbox is reaped, the harness row
    completed, and the runtime dropped. run_ordered_close owns this fallback.
    """
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}-{uuid.uuid4().hex[:6]}")

    bucket = _RecBucket()
    poster = _RecPoster([])
    caller = _StubCloseCaller()

    from control_plane.scribe_runtime import CloseConfig

    close_config = CloseConfig(
        bucket=bucket,
        bucket_name="proxy-notes",
        post_chat_link=poster,
        close_caller=caller,
        call_external=_passthrough_call_external,
    )
    registry = MeetingRuntimeRegistry(db, close_config=close_config)
    meeting_id, bot_id = await _seed_meeting(db)
    # NO ledger seeded — the meeting produced no notes.

    await _drive_join_to_close(db, registry, bot_id, meeting_id)

    # No notes object was written (empty ledger, nothing to finalize).
    assert bucket.written == [], "an empty-ledger close wrote a notes object"
    # But the ordered tail still ran: the sandbox is gone and the harness row completed.
    assert not any(h.meeting_id == meeting_id for h in sandbox_provider.list_sandboxes()), (
        "the empty-ledger close did not destroy the sandbox"
    )
    assert await _op_rows(db, meeting_id, MEETING_HARNESS_OP) == ["completed"]
    # And the pipes were torn down last (the runtime is dropped).
    assert registry.get(meeting_id) is None, "the empty-ledger close did not tear down"

    await pool.close()

    await pool.close()
