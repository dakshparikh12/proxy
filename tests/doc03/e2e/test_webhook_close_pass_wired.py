"""AC-CLOSE-WIRED — the close pass RUNS on the real meeting-end path.

Gap DOC03-CLOSE-PASS-UNWIRED: ``scribe.close.run_close_pass`` — the entire close
pass that produces the permanent notes record (Sonnet enrichment over the folded
ledger + gap/pending backfill -> markdown -> GCS ``write_finalized_notes`` -> chat
link -> teardown, in that order) — had ZERO production callers. On a real meeting
end the Recall ``call_ended`` webhook drains the Scribe consumer and tears the
runtime down, but NEVER produces the permanent markdown deliverable: no GCS notes
object, no chat link, no ``meeting-close`` operation_runs row. The close pass only
ran when a test called ``run_close_pass`` directly.

This drives the REAL meeting-end entrypoint — ``harness.webhooks.drain_pending_webhooks``
on a Recall ``call_ended`` webhook row — NOT ``run_close_pass`` directly. A meeting
with a folded ``note_deltas`` ledger ends, and the drain must:

  * fold the durable ledger + gap/pending backfill and run the close pass so the
    permanent markdown notes object is written to GCS (create-only, gen 0);
  * post the notes chat link BEFORE tearing the runtime down (the mandatory order);
  * record a single ``operation_type='meeting-close'`` operation_runs row COMPLETED;
  * only THEN end the runtime (``registry.get`` returns None after).

The vendor boundaries (the Sonnet close caller, the GCS bucket, the chat poster)
are injected on the registry's close config exactly as ``build_real_seams`` injects
``call_external`` for the offline tier — the PRODUCT orchestration + wiring is real;
only the vendor edge is a recordable seam, never a product double.
"""
from __future__ import annotations

import os
import uuid

import pytest

from db import Database, open_pool, repos
from db.repos import notes as notes_repo

from harness.meeting_runtime import MeetingRuntimeRegistry
from harness.scribe_runtime import CloseConfig
from harness.webhooks import drain_pending_webhooks

_DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(
    not _DSN, reason="live Postgres (TEST_DATABASE_URL) not provisioned this session"
)


class _RecBucket:
    """Create-only GCS bucket double honouring if_generation_match=0 (record-only)."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def blob(self, name: str):  # noqa: ANN202 - test double
        outer = self

        class _Blob:
            generation = None

            def upload_from_string(self, markdown, *, content_type, if_generation_match):
                assert if_generation_match == 0  # create-only (the mandatory precondition)
                assert content_type == "text/markdown"
                outer.written.append(markdown)

            def reload(self):
                self.generation = 1

        return _Blob()


class _RecPoster:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, url: str) -> None:
        self.calls.append(url)


class _StubCloseCaller:
    """An honest StructuredCaller — the recordable Sonnet vendor seam (never a product double).

    Honours the caller contract (model / prompt / output_schema in -> a real
    StructuredResult out) so ``generate_structured_close`` re-validates it into a real
    ``FinalNotes`` and the whole product close orchestration runs for real. It merely
    records the prompt so the test can prove the FOLDED LEDGER reached the close call.
    """

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


@requires_pg
@pytest.mark.asyncio
async def test_call_ended_webhook_runs_close_pass_before_teardown() -> None:
    pool = await open_pool(_DSN)
    db = Database(pool, f"test-{os.getpid()}")

    bucket, poster, caller = _RecBucket(), _RecPoster(), _StubCloseCaller()

    async def _passthrough_call_external(op, *, service, unit_cost_usd=0.0):  # noqa: ANN001
        return await op()

    close_config = CloseConfig(
        bucket=bucket,
        bucket_name="proxy-notes",
        post_chat_link=poster,
        close_caller=caller,
        call_external=_passthrough_call_external,
    )
    registry = MeetingRuntimeRegistry(db, close_config=close_config)

    # A real meeting row with a launched Recall bot id.
    async with db.acquire() as conn:
        tenant = await conn.fetchrow(
            "INSERT INTO tenants (name) VALUES ($1) RETURNING id", f"t-{uuid.uuid4().hex[:8]}"
        )
        repo = await conn.fetchrow(
            "INSERT INTO repos (tenant_id, full_name, default_branch) VALUES ($1,$2,$3) RETURNING id",
            tenant["id"], "example/r", "main",
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
    meeting_id = meeting["id"]

    # Start the runtime via the real in_call join path.
    guid_in = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(
            conn, guid_in, {"event": "bot.in_call", "data": {"bot_id": bot_id}}
        )
    await drain_pending_webhooks(db, registry=registry)
    assert registry.get(str(meeting_id)) is not None

    # A folded ledger already lives in note_deltas (the durable cross-service object).
    async with db.acquire() as conn:
        await notes_repo.append_delta(
            conn, meeting_id=meeting_id, entry_id="d1", op="add",
            payload={"kind": "decision", "text": "Ship the retry backoff today."},
            window_start_s=0.0,
        )
        await notes_repo.append_delta(
            conn, meeting_id=meeting_id, entry_id="a1", op="add",
            payload={"kind": "action", "text": "Zed owns the applier fold.", "owner": "Zed"},
            window_start_s=5.0,
        )

    # END the meeting through the REAL webhook path.
    guid_end = f"wh-{uuid.uuid4().hex}"
    async with db.acquire() as conn:
        await repos.webhooks.insert_event(
            conn, guid_end, {"event": "bot.call_ended", "data": {"bot_id": bot_id}}
        )
    await drain_pending_webhooks(db, registry=registry)

    # 1) The permanent markdown notes object was written to GCS (create-only).
    assert bucket.written, "close pass did not write the finalized notes object to GCS"
    assert "retry backoff" in bucket.written[0], "the folded ledger did not reach the notes object"

    # 2) The notes chat link was posted (BEFORE teardown — order enforced in run_close_pass).
    assert poster.calls == ["gs://proxy-notes/meetings/{}/notes.md".format(meeting_id)]

    # 3) The close caller SAW the folded ledger (the real fold reached the Sonnet pass).
    assert caller.prompts, "the close model call never fired"
    assert "Ship the retry backoff today." in caller.prompts[0]

    # 4) Exactly one meeting-close operation_runs row, COMPLETED (never a close_jobs table).
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status FROM operation_runs "
            "WHERE scope_id = $1 AND operation_type = 'meeting-close'",
            str(meeting_id),
        )
    assert [r["status"] for r in rows] == ["completed"], rows

    # 5) Only THEN is the runtime torn down.
    assert registry.get(str(meeting_id)) is None, "runtime was not ended after the close pass"
