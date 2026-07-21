"""AC-COAL-14..18 (+NEG) — the applier / transactional coupling / re-claim idempotency.

Every criterion here has ``dependency_class: db:postgres`` and a ``mock_boundary``
that FORBIDS an in-memory substitute ("real db only" / "MUST NOT stub the db seam").
Postgres is DOWN this session, so the honest thing is to write the real oracle and
SKIP it — never fake a pass on a stubbed DB.

The oracles below are written against the production ``scribe.apply_delta`` seam
(``services/scribe/src/scribe/notes.py``: the note-delta append AND the
``pending -> comprehended`` flip in ONE transaction) so they are integration-tier PLACEHOLDERS to be authored against the real Postgres seam (apply_note_delta(db, segment_id, delta)) once a database is available
a real Postgres is available. Two structural (static) checks that do NOT need a live
DB run unconditionally: the single-writer-per-meeting invariant is enforced by the
serial pipeline's shape, and the pipeline calls the applier exactly once per window.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

_PG_SKIP = pytest.mark.skip(
    reason="integration tier: Postgres not available this session"
)

_SCRIBE_SRC = (
    pathlib.Path(__file__).resolve().parents[3]
    / "services" / "scribe" / "src" / "scribe"
)


# ---------------------------------------------------------------------------
# AC-COAL-15-NEG — single-writer-per-meeting is STRUCTURAL (static, runs always).
# The DB-fault-injection negative tier is skipped (needs real db), but the
# structural "no concurrent apply path exists" check needs no DB.
# ---------------------------------------------------------------------------
def test_coal_15neg_single_writer_structural_no_concurrent_apply_path() -> None:
    from scribe import pipeline

    source = inspect.getsource(pipeline)
    tree = ast.parse(source)
    # apply_delta is awaited from exactly ONE place — the serial _process_one —
    # and never inside a gather/task-group/create_task, so two applies for the
    # same meeting cannot interleave (single writer without a lock-per-write).
    apply_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            inner = node.value
            if isinstance(inner, ast.Call):
                fn = inner.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "apply_delta":
                    apply_calls += 1
    # THRESHOLD concurrent_write_paths_per_meeting_allowed: 0.
    assert apply_calls == 1, "apply_delta must have exactly one serial call site"
    # No fan-out primitive is actually *called* (AST, not a docstring substring):
    # no gather / create_task / TaskGroup on the per-meeting apply path.
    fanout_attrs = {"gather", "create_task", "wait", "as_completed"}
    fanout_names = {"TaskGroup", "ThreadPoolExecutor", "ProcessPoolExecutor"}
    fanout_hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in fanout_attrs:
                fanout_hits.append(fn.attr)
            if isinstance(fn, ast.Name) and fn.id in fanout_names:
                fanout_hits.append(fn.id)
    assert fanout_hits == [], f"fan-out call on the apply path: {fanout_hits}"


@_PG_SKIP
def test_coal_15neg_concurrent_applier_writes_rejected_real_db() -> None:
    # Real-db fault-injection negative: attempt two concurrent applies on the same
    # meeting notes object and assert the single-writer path makes it impossible.
    raise AssertionError("requires real Postgres")


# ---------------------------------------------------------------------------
# AC-COAL-14 — dropped window recorded as a comprehension gap (real db).
# ---------------------------------------------------------------------------
@_PG_SKIP
def test_coal_14_dropped_span_recorded_as_comprehension_gap() -> None:
    # after drop: assert transcript_segments row for the span has
    # status=='comprehension_gap' with drop_reason populated; no unmarked hole.
    raise AssertionError("requires real Postgres")


@_PG_SKIP
def test_coal_14neg_no_gap_entry_when_window_succeeds() -> None:
    # after successful apply: status=='comprehended' and gap_entry_written==false.
    raise AssertionError("requires real Postgres")


# ---------------------------------------------------------------------------
# AC-COAL-15 — add mints new id; patch supersedes-not-erases prior value (real db).
# ---------------------------------------------------------------------------
@_PG_SKIP
def test_coal_15_add_mints_new_id_patch_supersedes_prior_value() -> None:
    # after add: entry_count += 1 and new id unique; after patch: E1.value==new,
    # E1.prior_value==original (superseded, not erased).
    raise AssertionError("requires real Postgres")


# ---------------------------------------------------------------------------
# AC-COAL-16 — a fact stated N times is one entry patched, not N rows (real db).
# ---------------------------------------------------------------------------
@_PG_SKIP
def test_coal_16_repeated_fact_folds_to_one_entry() -> None:
    # 50-repetition fixture: notes.entries_for_fact_X == 1, no duplicate rows.
    raise AssertionError("requires real Postgres")


@_PG_SKIP
def test_coal_16neg_two_distinct_facts_stay_two_entries() -> None:
    # F1 and F2 each once: total_entries == 2; patch-in-place doesn't collapse them.
    raise AssertionError("requires real Postgres")


# ---------------------------------------------------------------------------
# AC-COAL-17 — delta append AND pending->comprehended flip in ONE tx (real db).
# ---------------------------------------------------------------------------
@_PG_SKIP
def test_coal_17_append_and_flip_in_one_transaction() -> None:
    # intercept the Postgres transaction boundary; assert exactly ONE commit
    # contains both the note-delta INSERT and the transcript_segments UPDATE
    # where status='comprehended'.
    raise AssertionError("requires real Postgres")


@_PG_SKIP
def test_coal_17neg_crash_mid_apply_rolls_back_both() -> None:
    # inject a crash after the delta INSERT, before the flip; after rollback:
    # notes-delta absent AND transcript_segments.status=='pending'.
    # (scribe.notes.apply_note_delta exposes _fail_after_flip for exactly this.)
    raise AssertionError("requires real Postgres")


# ---------------------------------------------------------------------------
# AC-COAL-18 — pending window reprocessed exactly once, no double-count (real db).
# ---------------------------------------------------------------------------
@_PG_SKIP
def test_coal_18_pending_window_reprocessed_exactly_once() -> None:
    # after re-claim: status=='comprehended' AND notes_entry_count_delta==1;
    # a second re-claim attempt is a no-op (idempotency via comprehended guard).
    raise AssertionError("requires real Postgres")


@_PG_SKIP
def test_coal_18neg_comprehended_window_not_reprocessed() -> None:
    # re-claim scan predicate selects only status=='pending'; a comprehended
    # window appears zero times in the candidate set.
    raise AssertionError("requires real Postgres")
