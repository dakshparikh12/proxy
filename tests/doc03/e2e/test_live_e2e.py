"""LIVE end-to-end proof of the doc03 pipeline on REAL infrastructure (gcs+db+vendor).

A genuine meeting run — NO fakes, NO cassettes: real Claude (scribe per window + Sonnet
close), real Postgres (note_deltas append → fold), real object-versioned GCS (finalized
notes create-only). Gated on ``DOC03_LIVE_E2E=1`` + the real env, so CI (replay-only) skips
it; run it live to prove the whole pipeline composes end to end and the notes are coherent.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

_LIVE = os.environ.get("DOC03_LIVE_E2E") == "1"
_HAVE = bool(os.environ.get("TEST_DATABASE_URL") and os.environ.get("DOC03_STORE_GCS_BUCKET")
             and os.environ.get("ANTHROPIC_API_KEY"))

pytestmark = pytest.mark.e2e

requires_live = pytest.mark.skipif(
    not (_LIVE and _HAVE),
    reason="live E2E: set DOC03_LIVE_E2E=1 + TEST_DATABASE_URL + DOC03_STORE_GCS_BUCKET + "
           "ANTHROPIC_API_KEY (funded) to run the real full-pipeline proof (no cassette).",
)

_TRANSCRIPT = [
    ("Ana", "Okay let's start — first item is the checkout refactor.", 0, 4),
    ("Zed", "Right. I think we should ship the retry logic today.", 4, 8),
    ("Ana", "Agreed, that's final. I'll own the retry backoff update.", 8, 13),
    ("Mel", "Wait — is the build green? I thought it was red earlier.", 13, 17),
    ("Zed", "It's green now, I just re-checked.", 17, 20),
    ("Ana", "Good. Zed, can you review the PR by Friday?", 20, 24),
    ("Zed", "Sure, I'll get the PR reviewed by Friday.", 24, 27),
    ("Mel", "Should we put the retry logic behind a feature flag?", 27, 32),
    ("Ana", "Good question — let's decide next week. Also we must add a rollback switch before shipping.", 32, 39),
    ("Zed", "Agreed on the rollback switch.", 39, 42),
]


@requires_live
def test_live_full_pipeline_real_infra() -> None:
    os.environ["PROXY_MODEL_SCRIBE"] = "claude-haiku-4-5"
    os.environ["PROXY_MODEL_SCRIBE_CLOSE"] = "claude-sonnet-4-6"  # close is Sonnet-class by law

    import asyncpg
    from db.repos.notes import append_delta, load_deltas
    from google.cloud import storage
    from scribe.call import scribe_call
    from scribe.close import (
        CloseInput,
        GapPendingSpan,
        anthropic_structured_caller,
        assert_not_haiku,
        generate_structured_close,
        render_markdown,
        resolve_close_model,
    )
    from scribe.coalescer import Coalescer, TranscriptSegment
    from scribe.notes_artifact import read_notes_version, write_finalized_notes
    from scribe.notes_reader import Notes
    from scribe.prefix import MeetingHeader

    from libs.http.src.http.external import anthropic_client, call_external

    async def run() -> dict:
        meeting_id = uuid.uuid4()
        coal = Coalescer(time_cap_s=15.0)
        windows = []
        for spk, text, a, b in _TRANSCRIPT:
            windows += coal.feed(TranscriptSegment(speaker=spk, text=text, start_s=float(a),
                                                   end_s=float(b), token_count=max(1, len(text) // 4)))
        windows += coal.flush()

        meeting = MeetingHeader(meeting_id=str(meeting_id), agenda="Checkout refactor + retry logic",
                                participants=("Ana", "Zed", "Mel"), glossary={})
        client = anthropic_client()
        pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=3)

        total_ops = 0
        async with pool.acquire() as conn:
            for i, w in enumerate(windows):
                try:
                    delta = await scribe_call(meeting, "", w, call_external=call_external, client=client)
                except Exception:
                    continue  # dropped window — pipeline advances (§3.1)
                for j, op in enumerate(getattr(delta, "ops", [])):
                    payload = op.model_dump(mode="json") if hasattr(op, "model_dump") else {"raw": str(op)}
                    await append_delta(conn, meeting_id=meeting_id, entry_id=f"w{i}-{j}",
                                       op=getattr(op, "op", "add"), payload=payload, window_start_s=w.start_s)
                    total_ops += 1
            rows = await load_deltas(conn, meeting_id)
        for r in rows:
            if isinstance(r.get("payload"), str):
                r["payload"] = json.loads(r["payload"])
        Notes.fold_all(rows)  # durable read path must fold without error

        ledger = "\n".join(f"[{r['op']}] {json.dumps(r['payload'].get('entry', r['payload']))}" for r in rows)
        close_input = CloseInput(
            folded_ledger=ledger,
            gap_pending_spans=(GapPendingSpan(segment_id="s-gap", status="gap",
                                              text="Mel: double-check the rollback switch works before shipping."),),
        )
        model = assert_not_haiku(resolve_close_model())
        final, cost = await generate_structured_close(
            close_input, model=model, caller=anthropic_structured_caller(client), call_external=call_external)
        md = render_markdown(final)

        bucket = storage.Client().get_bucket(os.environ["DOC03_STORE_GCS_BUCKET"])
        gen = write_finalized_notes(bucket, str(meeting_id), md, if_generation_match=0)
        read_back = read_notes_version(bucket, str(meeting_id), gen)
        await pool.close()
        return {"windows": len(windows), "ops": total_ops, "md": md, "gen": gen,
                "read_back": read_back, "final": final, "cost": cost}

    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(run())
    finally:
        loop.close()

    text = r["md"].lower()
    print(f"\n[LIVE E2E] {r['windows']} windows, {r['ops']} note_deltas persisted, "
          f"close cost=${r['cost']:.4f}, gcs gen={r['gen']}\n{r['md']}")
    # The pipeline must have really run and produced coherent, persisted, published notes.
    assert r["windows"] >= 2, "coalescer should cut multiple windows from a 42s meeting"
    assert r["ops"] > 0, "the real scribe must have extracted + persisted at least one note delta"
    assert r["final"].summary.strip() and (r["final"].decisions or r["final"].action_items)
    assert "retry" in text and "ship" in text, "the ship-the-retry-logic decision must survive to the notes"
    assert "rollback" in text, "the gap-span content (rollback switch) must be backfilled by the close"
    assert "[contradicts" not in text, "the final record must carry no unresolved contradicts link"
    assert r["gen"] > 0 and r["read_back"] == r["md"], "create-only GCS write must round-trip"
