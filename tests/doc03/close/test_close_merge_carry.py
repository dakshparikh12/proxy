"""A22 (C-CLOSEMERGE) — the over-threshold reduce path carries ALL FinalNotes fields.

Before the fix, ``_merge_final_notes`` folded only summary/decisions/action_items/
open_questions, silently DROPPING the §2.6 header (title/date/attendees), the
worst-news-first ``blockers_risks``, ``proxy_actions`` (each with its Law-1 receipt),
``transcript_ref``, and — because it flattened — the decision provenance. On a large
meeting that entered the chunk-reduce path, a live blocker or a Proxy-action receipt
would never reach the reduce prompt and would be lost from the final notes.

These run under plain pytest — the real ``_merge_final_notes`` / ``reduce_close`` /
``CloseInput.to_prompt`` product path runs; the only injected seam is the honest
``StructuredCaller`` double (contract-honouring, not a Mock of the seam).
"""
from __future__ import annotations

import pytest

from scribe import close
from scribe.close import (
    CloseInput,
    FinalActionItem,
    FinalDecision,
    FinalNotes,
    ProxyAction,
    ReduceResult,
    _merge_final_notes,
    chunk_folded_ledger,
    reduce_close,
    should_chunk_reduce,
)

pytestmark = pytest.mark.asyncio


async def real_call_external(op, *, service, unit_cost_usd=0.0):
    class _Outcome:
        def __init__(self, value):
            self.value = value

    return _Outcome(await op())


# ── The merge carries every field (no silent drop) ────────────────────────────
def test_merge_carries_header_blockers_proxy_actions_and_provenance() -> None:
    p1 = FinalNotes(
        title="Q3 planning",
        meeting_date="2026-01-01",
        attendees=["Sam", "Zed"],
        blockers_risks=["prod DB migration is not reversible"],
        summary="chunk one",
        decisions=[FinalDecision(text="ship Friday", owner="Sam", when="Fri")],
        proxy_actions=[
            ProxyAction(kind="draft", text="drafted the rollback plan", receipt="pr#42")
        ],
        transcript_ref="gs://bucket/transcript.txt",
    )
    p2 = FinalNotes(
        attendees=["Zed", "Ada"],  # overlap with p1 -> deduped
        blockers_risks=["staging is down"],
        summary="chunk two",
        action_items=[FinalActionItem(text="review the PR", owner="Zed", due="Mon")],
        proxy_actions=[
            ProxyAction(kind="work", text="pinned the incident thread", receipt="log#7")
        ],
    )

    merged = _merge_final_notes([p1, p2])

    # §2.6 header survived (first-present title/date; deduped attendees union).
    assert merged.title == "Q3 planning"
    assert merged.meeting_date == "2026-01-01"
    assert merged.attendees == ["Sam", "Zed", "Ada"]  # order-preserving dedupe
    # Worst-news-first blockers from BOTH chunks survived (deduped).
    assert merged.blockers_risks == [
        "prod DB migration is not reversible",
        "staging is down",
    ]
    # Decision provenance carried WHOLE (owner/when intact, not flattened to text).
    assert merged.decisions[0].owner == "Sam" and merged.decisions[0].when == "Fri"
    assert merged.action_items[0].owner == "Zed"
    # 'What Proxy did' receipts from both chunks survived.
    assert [a.text for a in merged.proxy_actions] == [
        "drafted the rollback plan",
        "pinned the incident thread",
    ]
    assert merged.proxy_actions[0].receipt == "pr#42"
    # Raw-transcript pointer carried (first present).
    assert merged.transcript_ref == "gs://bucket/transcript.txt"


def test_merge_first_present_title_skips_empty_chunk() -> None:
    p_empty = FinalNotes(summary="no header here")
    p_titled = FinalNotes(title="The Real Title", summary="has header")
    merged = _merge_final_notes([p_empty, p_titled])
    assert merged.title == "The Real Title"


# ── The reduce prompt announces the merged block as already-resolved sections ──
def test_reduce_prompt_marks_resolved_sections() -> None:
    # Default (map) input is a raw folded ledger.
    raw = CloseInput(folded_ledger="RAW", gap_pending_spans=())
    assert "<folded_ledger>" in raw.to_prompt()
    assert "resolved" not in raw.to_prompt().lower().split("</folded_ledger>")[0].split(
        "<folded_ledger>"
    )[0]
    # The reduce input announces the block as already-resolved sections.
    reduce = CloseInput(
        folded_ledger="MERGED", gap_pending_spans=(), resolved_sections=True
    )
    prompt = reduce.to_prompt()
    assert "<resolved_sections>" in prompt
    assert "ALREADY-RESOLVED" in prompt
    assert "de-duplicate" in prompt


# ── End-to-end: over-threshold reduce preserves a blocker from a chunk ─────────
class _BlockerCarryingCaller:
    """Honest StructuredCaller: each map emits a per-chunk blocker; the reduce is
    fed ``render_markdown(merged)`` and must SEE the merged blockers (proving the
    carry reaches the reduce prompt)."""

    def __init__(self) -> None:
        self.count = 0
        self.reduce_prompt: str | None = None

    async def __call__(self, *, model, prompt, output_schema):
        self.count += 1
        if "<resolved_sections>" in prompt:
            # This is the reduce call — record the prompt it saw.
            self.reduce_prompt = prompt
            data = FinalNotes(summary="final", blockers_risks=[]).model_dump()
        else:
            # A map call — emit a chunk-local blocker keyed by call number.
            data = FinalNotes(
                summary=f"chunk {self.count}",
                blockers_risks=[f"blocker-from-chunk-{self.count}"],
            ).model_dump()
        return close.StructuredResult(data=data, total_cost_usd=0.01)


async def test_over_threshold_reduce_prompt_sees_merged_blockers() -> None:
    threshold = 100
    over = "".join(f"ledger entry {i} with content\n" for i in range(500))
    assert should_chunk_reduce(over, threshold=threshold) is True
    n_chunks = len(chunk_folded_ledger(over, threshold=threshold))
    assert n_chunks > 1

    caller = _BlockerCarryingCaller()
    result = await reduce_close(
        CloseInput(folded_ledger=over, gap_pending_spans=()),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
        threshold=threshold,
    )
    assert isinstance(result, ReduceResult)
    # The reduce call actually ran and its prompt carried EVERY chunk's blocker
    # (the carry reached the reduce step — the pre-fix bug dropped them all).
    assert caller.reduce_prompt is not None
    for i in range(1, n_chunks + 1):
        assert f"blocker-from-chunk-{i}" in caller.reduce_prompt
