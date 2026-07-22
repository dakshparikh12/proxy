"""Reality (vendor:anthropic) + integration (gcs:objects) tiers for the CLOSE PASS.

The vendor:anthropic tests drive the REAL ``generate_structured_close`` — the concrete
:func:`scribe.close.anthropic_structured_caller` (a forced-tool ``generateStructured``
on the Messages API) through the true ``call_external`` seam — against a vcrpy cassette
recorded once against the funded key. The seam, the caller, the request builder, and the
FinalNotes re-validation are REAL; the cassette only supplies the recorded body
(mock_boundary: MUST NOT replace the seam/caller). The gcs:objects tests hit a REAL
object-versioned bucket (``DOC03_CLOSE_GCS_BUCKET``).

Covers REALITY rung of AC-CLOSE-01/-03/-06/-11/-12 and integration rung of
AC-CLOSE-08/-14.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import uuid

import pytest
from scribe.close import (
    CloseInput,
    FinalNotes,
    GapPendingSpan,
    anthropic_structured_caller,
    assert_not_haiku,
    generate_structured_close,
    resolve_close_model,
)
from scribe.notes_artifact import (
    NotesGenerationConflictError,
    read_notes_version,
    write_finalized_notes,
)

from libs.http.src.http.external import anthropic_client, call_external

_CLOSE_GCS_BUCKET = os.environ.get("DOC03_CLOSE_GCS_BUCKET", "").strip()
requires_gcs = pytest.mark.skipif(
    not _CLOSE_GCS_BUCKET,
    reason=(
        "integration tier: set DOC03_CLOSE_GCS_BUCKET (a real bucket with Object "
        "Versioning ON) to run the live if_generation_match=0 create-only oracle"
    ),
)


def _close_bucket() -> object:
    from google.cloud import storage  # lazy: SDK only needed for the live tier

    return storage.Client().get_bucket(_CLOSE_GCS_BUCKET)

_CLOSE_MODEL = "claude-sonnet-4-6"
_CASSETTES = pathlib.Path(__file__).resolve().parent.parent.parent / "cassettes"
_GOLDEN = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "doc03" / "golden"


def _norm_tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _best_overlap(text: str, candidates: list[str]) -> float:
    """Max Jaccard token overlap of ``text`` against any candidate (0..1)."""
    t = _norm_tokens(text)
    if not t:
        return 0.0
    return max((len(t & _norm_tokens(c)) / len(t | _norm_tokens(c)) for c in candidates), default=0.0)


def _parse_json_object(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        t = "\n".join(lines[1 : -1 if lines[-1].strip() == "```" else len(lines)]).strip()
    match = re.search(r"\{.*\}", t, re.S)
    return json.loads(match.group(0) if match else t)


def _semantic_coverage_score(candidate_texts: list[str], golden_texts: list[str]) -> float:
    """Fraction (0..1) of GOLDEN items semantically covered by the CANDIDATE, graded by a
    REAL LLM judge (paraphrase-robust — the close legitimately rewords items, so a token
    metric under-scores it). One Haiku call through the true seam; recorded to the same
    cassette as the close call (interaction 1). This is the AC-CLOSE-12 golden-key scorer."""
    client = anthropic_client()
    prompt = (
        "You grade a meeting-notes close pass. Given GOLDEN notes (expected content) and "
        "CANDIDATE notes (produced content), return the fraction (0.0-1.0) of GOLDEN items "
        "semantically covered by some CANDIDATE item — ALLOW paraphrase and rewording. "
        'Return ONLY JSON: {"score": <float 0..1>}.\n\nGOLDEN:\n'
        + "\n".join(f"- {g}" for g in golden_texts)
        + "\n\nCANDIDATE:\n"
        + "\n".join(f"- {c}" for c in candidate_texts)
    )

    async def _op():
        return await client.messages.create(
            model="claude-haiku-4-5", max_tokens=100, messages=[{"role": "user", "content": prompt}]
        )

    outcome = _run(call_external(_op, service="anthropic"))
    resp = getattr(outcome, "value", outcome)
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    return float(_parse_json_object(text)["score"])


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _ensure_key(monkeypatch):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-vcr-replay-placeholder")
    monkeypatch.setenv("PROXY_MODEL_SCRIBE_CLOSE", _CLOSE_MODEL)  # Sonnet-class close seat


# A folded ledger with a DUPLICATE action item + a live 'contradicts' link, plus a gap
# span whose content the close must fold in (AC-CLOSE-12 dedup / resolve / gap-backfill).
_LEDGER = (
    "SUMMARY: The team discussed the checkout refactor and the retry logic.\n"
    "DECISIONS:\n- We will ship the retry logic today (final).\n"
    "ACTION ITEMS:\n"
    "- Ana to update the retry backoff logic.\n"
    "- Ana to update the retry backoff logic (this is a duplicate of the item above).\n"
    "- Zed to review the PR by Friday.\n"
    "CLAIMS:\n- The build is green. [contradicts c1: earlier Ana said the build was red]\n"
    "OPEN QUESTIONS:\n- Should we put the retry logic behind a feature flag?\n"
)
_GAP = GapPendingSpan(
    segment_id="s9",
    text="Mel: before shipping we also agreed to add a rollback switch.",
    status="gap",
)


def _close_input() -> CloseInput:
    return CloseInput(folded_ledger=_LEDGER, gap_pending_spans=(_GAP,))


def _do_close() -> tuple[FinalNotes, float | None]:
    model = assert_not_haiku(resolve_close_model())  # Sonnet-class seat, fail-fast if Haiku
    return _run(
        generate_structured_close(
            _close_input(), model=model, caller=anthropic_structured_caller(), call_external=call_external
        )
    )


def _cassette_request(test_name: str) -> dict:
    doc = json.loads(_dump_yaml(_CASSETTES / f"{test_name}.yaml"))
    body = doc["interactions"][0]["request"]["body"]
    if isinstance(body, dict):
        body = body.get("string", "")
    return json.loads(body)


def _dump_yaml(path: pathlib.Path) -> str:
    import yaml

    return json.dumps(yaml.safe_load(path.read_text()))


@pytest.mark.reality
@pytest.mark.vcr
def test_ac_close_01_golden_path_real_vendor() -> None:
    """AC-CLOSE-01 (reality): one real ``generateStructured`` reduce over the folded ledger
    + gap backfill produces a valid, non-empty FinalNotes object (the vendor:anthropic core
    of the golden path; the GCS write + chat post are covered by their own tiers)."""
    final, _cost = _do_close()
    assert isinstance(final, FinalNotes)
    assert final.summary.strip()
    assert final.action_items or final.decisions  # real content extracted


@pytest.mark.reality
@pytest.mark.vcr
def test_ac_close_03_reality_model_id_is_sonnet_in_cassette() -> None:
    """AC-CLOSE-03 (reality): the transmitted request carries the resolved Sonnet-class
    close seat — ``model == PROXY_MODEL_SCRIBE_CLOSE`` and 'haiku' not in it."""
    _do_close()
    req = _cassette_request("test_ac_close_03_reality_model_id_is_sonnet_in_cassette")
    assert req["model"] == _CLOSE_MODEL
    assert "haiku" not in req["model"].lower()


@pytest.mark.reality
@pytest.mark.vcr
def test_ac_close_06_reality_generatestructured_surface() -> None:
    """AC-CLOSE-06 (reality): the call is a forced-tool ``generateStructured`` whose
    output schema IS the FinalNotes JSON Schema (outputFormat:{type:'json_schema'}), and
    the result is Pydantic re-validated into FinalNotes."""
    final, _cost = _do_close()
    req = _cassette_request("test_ac_close_06_reality_generatestructured_surface")
    assert req.get("tools"), "structured output must go through a tool (generateStructured surface)"
    assert req["tool_choice"]["type"] == "tool"  # forced, not free text (not a bare messages.create)
    schema = req["tools"][0]["input_schema"]
    assert schema.get("title") == "FinalNotes" or "properties" in schema  # the json_schema output format
    assert isinstance(final, FinalNotes)  # re-validated through model_validate


@pytest.mark.reality
@pytest.mark.vcr
def test_ac_close_11_reality_cost_gt_zero() -> None:
    """AC-CLOSE-11 (reality): ``total_cost_usd`` read off the result is > 0.0 for a real
    model call (the close layer reads it, never recomputes via token arithmetic)."""
    _final, cost = _do_close()
    assert cost is not None and cost > 0.0


@pytest.mark.reality
@pytest.mark.vcr
def test_ac_close_12_reality_dedup_and_conflict_resolution() -> None:
    """AC-CLOSE-12 (reality/eval): the REAL close output over a ledger with a duplicate
    action item + a live contradiction + a gap span is (1) deduplicated, (2) has the gap
    content backfilled, (3) carries NO contradicts link, and (4) matches the committed
    golden key by content-coverage >= 0.90. Scored against
    ``fixtures/doc03/golden/close_dedup_gap_resolved.json``."""
    final, _cost = _do_close()
    golden = json.loads((_GOLDEN / "close_dedup_gap_resolved.json").read_text())

    action_texts = [a.text for a in final.action_items]
    all_text = " ".join(
        [final.summary]
        + [d.text for d in final.decisions]
        + action_texts
        + [q.text for q in final.open_questions]
    ).lower()

    # (1) dedup_completeness == 1.0 — no two action items are near-duplicates.
    dup_pairs = sum(
        1
        for i in range(len(action_texts))
        for j in range(i + 1, len(action_texts))
        if _best_overlap(action_texts[i], [action_texts[j]]) >= 0.8
    )
    assert dup_pairs == 0, f"duplicate action items survived the close: {action_texts}"

    # (2) gap_backfill_completeness == 1.0 — the gap span's content is folded in.
    assert "rollback" in all_text, "gap-span content (the rollback switch) was not backfilled"

    # (3) contradicts_links_in_output == 0 — the definitive record carries no contradicts marker.
    assert "[contradicts" not in all_text and "contradicts" not in all_text

    # (4) semantic coverage vs the golden key >= 0.90, graded by a real LLM judge.
    #     Action items carry owner/due in STRUCTURED fields — fold them into the text the
    #     judge sees so "Review the PR (owner Zed, due Friday)" matches the golden item.
    def _ai_full(a) -> str:
        extra = [x for x in (f"owner {a.owner}" if a.owner else "", f"due {a.due}" if a.due else "") if x]
        return a.text + (f" ({', '.join(extra)})" if extra else "")

    candidate = (
        [d.text for d in final.decisions]
        + [_ai_full(a) for a in final.action_items]
        + [q.text for q in final.open_questions]
    )
    golden_texts = (
        [d["text"] for d in golden["decisions"]]
        + [a["text"] for a in golden["action_items"]]
        + [q["text"] for q in golden["open_questions"]]
    )
    score = _semantic_coverage_score(candidate, golden_texts)
    assert score >= 0.90, f"close output semantically covers only {score:.2f} of the golden key (< 0.90)"


# ===========================================================================
# INTEGRATION — a real object-versioned GCS bucket (gcs:objects).
# ===========================================================================
@pytest.mark.integration
@requires_gcs
def test_ac_close_08_real_gcs_create_only_generation() -> None:
    """AC-CLOSE-08 (integration): a real GCS write with ``if_generation_match=0`` creates
    the finalized-notes object with generation > 0 — no overwrite is possible through this
    create-only path."""
    bucket = _close_bucket()
    mid = f"m-close-08-{uuid.uuid4().hex}"
    gen = write_finalized_notes(bucket, mid, "# Meeting notes\n\ncreate-only close.\n", if_generation_match=0)
    assert isinstance(gen, int) and gen > 0
    assert read_notes_version(bucket, mid, gen).startswith("# Meeting notes")


@pytest.mark.integration
@requires_gcs
def test_ac_close_14_real_gcs_recovery_precondition() -> None:
    """AC-CLOSE-14 (integration): a SECOND create-only attempt raises the precondition
    conflict; the object generation is unchanged and only the FIRST content survives — a
    close re-run after a crash never overwrites the already-written permanent record."""
    bucket = _close_bucket()
    mid = f"m-close-14-{uuid.uuid4().hex}"
    gen1 = write_finalized_notes(bucket, mid, "first close output", if_generation_match=0)
    with pytest.raises(NotesGenerationConflictError):
        write_finalized_notes(bucket, mid, "second close output", if_generation_match=0)
    assert read_notes_version(bucket, mid, gen1) == "first close output"  # first content stands
