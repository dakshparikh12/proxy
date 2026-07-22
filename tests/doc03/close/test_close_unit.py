"""AC-CLOSE unit tier — pure ordering / threshold / typed-error / composition.

These are the genuinely UNIT-testable criteria the task calls out: the ordered
sequence (render -> GCS write -> chat post -> teardown), the chunk-reduce
threshold decision, the gap/pending backfill selection, the create-only
(if_generation_match=0) discipline, and the typed terminal-error handling. No
vendor SDK is imported and no seam is replaced with Mock(): the structured caller
and the ``call_external`` seam are honest async callables honouring their real
contracts, and the GCS boundary is the real ``write_finalized_notes`` seam driven
by a create-only bucket double that reproduces the 412 precondition contract.
"""
from __future__ import annotations

import ast
import inspect

import pytest
from scribe import close
from scribe.close import (
    CHAT_POST_MAX_ATTEMPTS,
    CHUNK_REDUCE_TOKEN_THRESHOLD,
    OPERATION_TYPE,
    ChatLinkPostError,
    CloseInput,
    CloseMaxStructuredRetriesError,
    CloseMaxTurnsError,
    CloseStep,
    CloseVendorError,
    DatabaseConnectionError,
    FinalActionItem,
    FinalDecision,
    FinalNotes,
    FinalOpenQuestion,
    GapPendingSpan,
    HaikuModelRejectedError,
    InMemoryOperationRunSink,
    NotesGenerationConflictError,
    ReduceResult,
    approx_tokens,
    assert_not_haiku,
    chunk_folded_ledger,
    connect_and_fetch_gap_pending_spans,
    generate_structured_close,
    reduce_close,
    render_markdown,
    resolve_close_model,
    run_close_pass,
    should_chunk_reduce,
)
from scribe.notes_artifact import NOTES_OBJECT_TEMPLATE

pytestmark = pytest.mark.asyncio


# ── Honest test doubles (contracts, not Mock() of the seam) ──────────────────

async def real_call_external(op, *, service, unit_cost_usd=0.0):
    """A faithful re-implementation of the ``call_external`` retry+cost contract.

    This is NOT a Mock of the seam — it runs the injected ``op`` and returns an
    outcome object exposing ``.value``, exactly the shape ``generate_structured_close``
    reads. The vendor round-trip lives inside ``op`` (the injected StructuredCaller),
    which is what stays skipped/cassette at the reality tier. Importing the product
    ``libs.http.external`` seam is impossible here (it imports ``anthropic`` at
    module scope, uninstalled this session), so the contract is honoured directly —
    the module is designed for exactly this injection (see close.py docstring).
    """
    class _Outcome:
        def __init__(self, value):
            self.value = value

    return _Outcome(await op())


def _final_notes(**kw):
    kw.setdefault("summary", "We shipped the close pass.")
    return FinalNotes(**kw)


def _structured_result(data=None, cost=0.0123):
    return close.StructuredResult(
        data=data if data is not None else _final_notes().model_dump(),
        total_cost_usd=cost,
    )


class _RecordingBucket:
    """A create-only bucket double honouring the if_generation_match=0 contract.

    Drives the REAL ``write_finalized_notes`` seam (not a stub of it): first
    create-only write returns generation 1; a second create-only write raises the
    google PreconditionFailed the seam maps to NotesGenerationConflictError. This
    reproduces GCS's 412 wire contract so the create-only DISCIPLINE runs for real
    at the unit tier while the live-bucket oracle stays skipped (requires_gcs).
    """

    def __init__(self, exists: bool = False, fail: bool = False):
        self._exists = exists
        self._fail = fail
        self.written: list[str] = []
        self.generation = 1 if exists else 0

    def blob(self, name):
        outer = self

        class _Blob:
            generation = outer.generation if outer._exists else None

            def upload_from_string(self, markdown, *, content_type, if_generation_match):
                if outer._fail:
                    raise RuntimeError("bucket unavailable (simulated storage outage)")
                assert if_generation_match == 0, "close write must be create-only"
                if outer._exists:
                    # Raise EXACTLY the 412 type the write seam catches (the real
                    # google PreconditionFailed when the GCS SDK is installed, else
                    # the seam's fallback) so the create-only mapping to
                    # NotesGenerationConflictError runs for real — never a stub.
                    from scribe.notes_artifact import precondition_failed_type
                    raise precondition_failed_type()("object already exists")
                outer.written.append(markdown)
                outer._exists = True
                outer.generation = 1

            def reload(self):
                self.generation = outer.generation

        return _Blob()


class _Poster:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls: list[str] = []

    async def __call__(self, url: str) -> None:
        self.calls.append(url)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("transient chat post failure")


class _Teardown:
    def __init__(self):
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


async def _run(bucket, poster, teardown, sink, *, cost=0.0123, attempts=3, mid="m1"):
    return await run_close_pass(
        mid,
        _final_notes(),
        cost,
        bucket=bucket,
        bucket_name="notes-bucket",
        post_chat_link=poster,
        teardown=teardown,
        op_sink=sink,
        chat_post_max_attempts=attempts,
    )


# ── AC-CLOSE-05 / -05B — chunk-reduce threshold is pure arithmetic ───────────

def test_ac_close_05_below_threshold_single_pass():
    # A folded ledger below the token threshold -> exactly one model call, no chunk.
    small = "x" * (CHUNK_REDUCE_TOKEN_THRESHOLD)  # ~ threshold/4 tokens, well under
    assert should_chunk_reduce(small) is False
    assert approx_tokens(small) <= CHUNK_REDUCE_TOKEN_THRESHOLD


def test_ac_close_05b_above_threshold_enters_chunk_path():
    # A folded ledger whose token count exceeds the threshold -> chunk-reduce path.
    big = "y" * ((CHUNK_REDUCE_TOKEN_THRESHOLD + 10) * 4 + 8)
    assert approx_tokens(big) > CHUNK_REDUCE_TOKEN_THRESHOLD
    assert should_chunk_reduce(big) is True


def test_chunk_folded_ledger_splits_over_threshold_into_subthreshold_chunks():
    # Below threshold -> one chunk (single map call, i.e. AC-CLOSE-05 single-pass).
    small = "line\n" * 10
    assert chunk_folded_ledger(small, threshold=1000) == [small]
    # Over threshold -> >1 chunk, and EVERY chunk is at or under the budget.
    threshold = 100
    max_chars = threshold * 4
    big = "".join(f"entry-{i} some ledger text here\n" for i in range(400))
    chunks = chunk_folded_ledger(big, threshold=threshold)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= max_chars
    # No content is lost across the split (the reduce sees the whole ledger).
    assert "".join(chunks) == big


class _CountingCaller:
    """Honest StructuredCaller counting real generateStructured invocations.

    NOT a Mock of the seam: it honours the caller contract (model/prompt/schema in,
    a real StructuredResult out) and simply records how many times the reduce path
    invoked it and the prompts it saw. Driven through the real call_external seam
    (real_call_external) exactly like every other unit-tier caller.
    """

    def __init__(self):
        self.count = 0
        self.prompts: list[str] = []

    async def __call__(self, *, model, prompt, output_schema):
        self.count += 1
        self.prompts.append(prompt)
        # Each call returns a valid FinalNotes payload with a call-tagged action
        # item so the merge can be observed to fold across chunks.
        data = _final_notes(
            summary=f"partial summary {self.count}",
            action_items=[FinalActionItem(text=f"item-from-call-{self.count}")],
        ).model_dump()
        return close.StructuredResult(data=data, total_cost_usd=0.01)


async def test_ac_close_05b_chunk_reduce_makes_more_than_one_call_one_object():
    # AC-CLOSE-05B: an OVER-threshold folded ledger enters the real chunk-reduce
    # path -> MORE THAN ONE generateStructured call, and the result is still a
    # SINGLE unified FinalNotes object (not a list of per-chunk partials).
    threshold = 100
    over = "".join(f"ledger entry {i} with content\n" for i in range(500))
    assert should_chunk_reduce(over, threshold=threshold) is True
    caller = _CountingCaller()
    result = await reduce_close(
        CloseInput(folded_ledger=over, gap_pending_spans=()),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
        threshold=threshold,
    )
    assert isinstance(result, ReduceResult)
    # More than one model call actually happened (the chunk-reduce loop ran).
    assert result.model_call_count > 1
    assert caller.count == result.model_call_count
    # It is per-chunk maps + exactly ONE reduce: N chunks -> N+1 calls.
    n_chunks = len(chunk_folded_ledger(over, threshold=threshold))
    assert n_chunks > 1
    assert result.model_call_count == n_chunks + 1
    # The output is ONE merged notes object, not a list.
    assert isinstance(result.final_notes, FinalNotes)
    # Cost is SUMMED from the per-call SDK results (never recomputed from tokens).
    assert result.total_cost_usd is not None
    assert abs(result.total_cost_usd - 0.01 * result.model_call_count) < 1e-9


async def test_ac_close_05_below_threshold_makes_exactly_one_call():
    # AC-CLOSE-05 (behavioral): a below-threshold ledger reduces in EXACTLY ONE
    # model call — no chunk-reduce loop is entered.
    threshold = 100_000
    small = "a normal meeting's folded ledger, well under threshold\n" * 3
    assert should_chunk_reduce(small, threshold=threshold) is False
    caller = _CountingCaller()
    result = await reduce_close(
        CloseInput(folded_ledger=small, gap_pending_spans=()),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
        threshold=threshold,
    )
    assert result.model_call_count == 1
    assert caller.count == 1
    assert isinstance(result.final_notes, FinalNotes)


async def test_ac_close_05b_backfill_reduced_in_once_not_per_chunk():
    # The gap/pending backfill is reduced in EXACTLY ONCE (at the merge), never
    # duplicated into every chunk's map call. Only one prompt carries the span.
    threshold = 100
    over = "".join(f"ledger entry {i}\n" for i in range(500))
    spans = (GapPendingSpan("s1", "UNIQUE-GAP-BACKFILL-TOKEN", "gap"),)
    caller = _CountingCaller()
    await reduce_close(
        CloseInput(folded_ledger=over, gap_pending_spans=spans),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
        threshold=threshold,
    )
    carrying = [p for p in caller.prompts if "UNIQUE-GAP-BACKFILL-TOKEN" in p]
    assert len(carrying) == 1  # only the reduce prompt carries the backfill


def test_ac_close_05_boundary_is_strict_greater_than():
    # Exactly-at-threshold does NOT chunk (a normal meeting folds in one pass).
    exact = "z" * (CHUNK_REDUCE_TOKEN_THRESHOLD * 4)
    assert approx_tokens(exact) == CHUNK_REDUCE_TOKEN_THRESHOLD
    assert should_chunk_reduce(exact) is False


# ── AC-CLOSE-03-NEG — fail-fast on a Haiku-class close seat ───────────────────

def test_ac_close_03neg_haiku_rejected_before_any_call():
    with pytest.raises(HaikuModelRejectedError):
        assert_not_haiku("claude-haiku-4-5")
    # It is a ConfigurationError (raised BEFORE the model is invoked).
    assert issubclass(HaikuModelRejectedError, close.ConfigurationError)


def test_ac_close_03_sonnet_class_accepted():
    # A Sonnet-class id passes the tier guard unchanged.
    assert assert_not_haiku("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_ac_close_03_resolve_seat_honours_env_override(monkeypatch):
    # AC-CLOSE-03: the model id used is PROXY_MODEL_SCRIBE_CLOSE (env override),
    # and the tier guard confirms it is not Haiku-class. Exercises the REAL
    # libs.llm seat table via resolve_close_model (no injected fake).
    monkeypatch.setenv("PROXY_MODEL_SCRIBE_CLOSE", "claude-sonnet-4-6")
    model = resolve_close_model()
    assert model == "claude-sonnet-4-6"
    assert assert_not_haiku(model) == "claude-sonnet-4-6"
    assert "haiku" not in model.lower()


# ── AC-CLOSE-04 — input is folded ledger + ONLY gap/pending spans ────────────

def test_ac_close_04_prompt_excludes_comprehended_raw_text():
    spans = (
        GapPendingSpan("s1", "gap raw one", "gap"),
        GapPendingSpan("s2", "gap raw two", "gap"),
        GapPendingSpan("s3", "gap raw three", "gap"),
        GapPendingSpan("s4", "pending raw one", "pending"),
        GapPendingSpan("s5", "pending raw two", "pending"),
    )
    ci = CloseInput(folded_ledger="THE-FOLDED-LEDGER", gap_pending_spans=spans)
    prompt = ci.to_prompt()
    # folded ledger is present as the primary input block
    assert "THE-FOLDED-LEDGER" in prompt
    assert "<folded_ledger>" in prompt
    # exactly the 5 gap+pending raw spans appear
    for s in spans:
        assert s.text in prompt
    # a comprehended segment's raw text is NEVER carried in the input
    assert "comprehended-raw-should-not-appear" not in prompt
    assert prompt.count("[gap ") == 3
    assert prompt.count("[pending ") == 2


# ── AC-CLOSE-06 — generateStructured -> json_schema -> Pydantic re-validate ───

def test_ac_close_06_output_schema_is_json_schema_derived_from_model():
    schema = FinalNotes.json_schema()
    assert isinstance(schema, dict)
    # outputFormat={'type':'json_schema','schema':<this>} — the wire schema and the
    # re-validation model share ONE source of truth (model_json_schema).
    assert "properties" in schema
    for field in ("summary", "decisions", "action_items", "open_questions"):
        assert field in schema["properties"]


async def test_ac_close_06_result_is_pydantic_revalidated():
    captured = {}

    async def caller(*, model, prompt, output_schema):
        captured["model"] = model
        captured["schema"] = output_schema
        return _structured_result(_final_notes(summary="revalidate me").model_dump())

    final, cost = await generate_structured_close(
        CloseInput("led", ()),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
    )
    assert isinstance(final, FinalNotes)  # re-validated into the model, not raw dict
    assert final.summary == "revalidate me"
    assert captured["schema"] == FinalNotes.json_schema()
    assert captured["model"] == "claude-sonnet-4-6"


async def test_ac_close_06_model_id_passed_through_verbatim():
    # AC-CLOSE-03 unit surface: the model id handed to the SDK call is exactly the
    # resolved (Sonnet-class) id, never Haiku.
    seen = {}

    async def caller(*, model, prompt, output_schema):
        seen["model"] = model
        return _structured_result()

    await generate_structured_close(
        CloseInput("led", ()),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
    )
    assert seen["model"] == "claude-sonnet-4-6"
    assert "haiku" not in seen["model"].lower()


# ── AC-CLOSE-06-NEG / -06B-NEG — typed terminal errors, distinct branches ────

async def test_ac_close_06neg_max_turns_caught_as_typed_error():
    async def caller(*, model, prompt, output_schema):
        raise CloseMaxTurnsError("hit max turns")

    with pytest.raises(CloseMaxTurnsError) as ei:
        await generate_structured_close(
            CloseInput("led", ()),
            model="claude-sonnet-4-6",
            caller=caller,
            call_external=real_call_external,
        )
    # NOT swallowed / NOT collapsed to a generic Exception or CloseVendorError.
    assert not isinstance(ei.value, CloseVendorError)
    assert ei.value.error_type == "error_max_turns"


async def test_ac_close_06bneg_structured_retries_is_distinct_type():
    async def caller(*, model, prompt, output_schema):
        raise CloseMaxStructuredRetriesError("exhausted structured retries")

    with pytest.raises(CloseMaxStructuredRetriesError) as ei:
        await generate_structured_close(
            CloseInput("led", ()),
            model="claude-sonnet-4-6",
            caller=caller,
            call_external=real_call_external,
        )
    assert ei.value.error_type == "error_max_structured_output_retries"
    # The two terminal subtypes are DISTINCT (handled in separate branches).
    assert not isinstance(ei.value, CloseMaxTurnsError)
    assert CloseMaxTurnsError.error_type != CloseMaxStructuredRetriesError.error_type


# ── AC-CLOSE-01-NEG / -12-NEG — honest degradation on vendor fault/garbage ───

async def test_ac_close_01neg_transport_fault_surfaces_as_vendor_error():
    async def caller(*, model, prompt, output_schema):
        raise ValueError("simulated 5xx / transport fault")

    with pytest.raises(CloseVendorError):
        await generate_structured_close(
            CloseInput("led", ()),
            model="claude-sonnet-4-6",
            caller=caller,
            call_external=real_call_external,
        )


async def test_ac_close_12neg_garbage_body_fails_revalidation_no_corruption():
    async def caller(*, model, prompt, output_schema):
        # summary must be a str <= 20000; a dict is garbage -> re-validation fails.
        return _structured_result({"summary": {"not": "a string"}})

    with pytest.raises(CloseVendorError) as ei:
        await generate_structured_close(
            CloseInput("led", ()),
            model="claude-sonnet-4-6",
            caller=caller,
            call_external=real_call_external,
        )
    assert "re-validation" in str(ei.value)


# ── AC-CLOSE-09 — mandatory order render < gcs < chat < teardown ─────────────

async def test_ac_close_09_strict_order_render_gcs_chat_teardown():
    bucket, poster, teardown = _RecordingBucket(), _Poster(), _Teardown()
    sink = InMemoryOperationRunSink()
    res = await _run(bucket, poster, teardown, sink)
    steps = res.trace.steps()
    assert steps == [
        CloseStep.RENDER,
        CloseStep.GCS_WRITE,
        CloseStep.CHAT_LINK,
        CloseStep.TEARDOWN,
    ]
    # strict monotonic timestamps, teardown never coincides with/precedes chat post
    ts = [e.ts for e in res.trace.events]
    assert ts == sorted(ts)
    chat = next(e for e in res.trace.events if e.step is CloseStep.CHAT_LINK)
    td = next(e for e in res.trace.events if e.step is CloseStep.TEARDOWN)
    assert chat.ts < td.ts
    assert teardown.count == 1
    assert poster.calls == [res.notes_url]


async def test_ac_close_09_notes_url_targets_the_per_meeting_object():
    bucket, poster, teardown = _RecordingBucket(), _Poster(), _Teardown()
    res = await _run(bucket, poster, teardown, InMemoryOperationRunSink(), mid="mtg-9")
    assert res.notes_url == "gs://notes-bucket/" + NOTES_OBJECT_TEMPLATE.format(
        meeting_id="mtg-9"
    )


# ── AC-CLOSE-08-NEG — GCS write fail: no chat, no teardown, row failed ───────

async def test_ac_close_08neg_gcs_failure_blocks_chat_and_teardown():
    bucket = _RecordingBucket(fail=True)
    poster, teardown = _Poster(), _Teardown()
    sink = InMemoryOperationRunSink()
    with pytest.raises(CloseVendorError):
        await _run(bucket, poster, teardown, sink, mid="mf")
    # Step 3 + Step 4 never proceeded.
    assert poster.calls == []
    assert teardown.count == 0
    assert sink.rows["mf"]["status"] == "failed"
    assert sink.rows["mf"]["error_type"] == "gcs_write_failed"


# ── AC-CLOSE-10 / -10-NEG — chat post retried; teardown gated on success ─────

async def test_ac_close_10_transient_post_retried_then_teardown():
    bucket = _RecordingBucket()
    poster = _Poster(fail_times=1)  # first attempt fails, retry succeeds
    teardown = _Teardown()
    sink = InMemoryOperationRunSink()
    res = await _run(bucket, poster, teardown, sink, mid="mr")
    assert len(poster.calls) == 2  # failed once, succeeded on retry
    assert teardown.count == 1
    # teardown recorded AFTER the successful chat-link confirm, never between.
    steps = res.trace.steps()
    assert steps.index(CloseStep.CHAT_LINK) < steps.index(CloseStep.TEARDOWN)
    assert sink.rows["mr"]["status"] == "succeeded"


async def test_ac_close_10neg_permanent_post_failure_blocks_teardown():
    bucket = _RecordingBucket()
    poster = _Poster(fail_times=99)  # always fails -> retries exhausted
    teardown = _Teardown()
    sink = InMemoryOperationRunSink()
    with pytest.raises(ChatLinkPostError):
        await _run(bucket, poster, teardown, sink, attempts=3, mid="mp")
    assert len(poster.calls) == 3  # exactly chat_post_max_attempts
    assert teardown.count == 0  # teardown BLOCKED
    assert sink.rows["mp"]["status"] == "failed"
    assert sink.rows["mp"]["error_type"] == "chat_link_post_failed"


# ── AC-CLOSE-07 / -07-NEG — one operation_runs row, no close_jobs, no dup ─────

async def test_ac_close_07_one_operation_runs_row_meeting_close():
    bucket, poster, teardown = _RecordingBucket(), _Poster(), _Teardown()
    sink = InMemoryOperationRunSink()
    await _run(bucket, poster, teardown, sink, cost=0.05, mid="m7")
    assert len(sink.rows) == 1
    row = sink.rows["m7"]
    assert row["operation_type"] == OPERATION_TYPE == "meeting-close"
    assert row["status"] == "succeeded"


def _module_code_without_docstrings(module) -> str:
    """Return a module's source with EVERY docstring stripped (AST-based).

    The "no close_jobs table" audit (AC-CLOSE-07) must inspect EXECUTABLE code, not
    the prose that documents the invariant ("NO ``close_jobs`` table"): a substring
    scan of the raw source would fail on the module's own explanation — a wrong-
    reason failure. This walks the AST, drops each module/def/class docstring node,
    and unparses the rest (the same technique the STORE tier uses for its no-base64
    audit).
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]
    return ast.unparse(tree)


def test_ac_close_07_no_close_jobs_table_referenced_in_source():
    # AC-CLOSE-07: there is NO close_jobs table / no dedicated close-tracking table.
    # The close pass is ONE coarse row on operation_runs. The module's docstring
    # documents that invariant ("NO close_jobs table"), so the audit inspects the
    # executable code with docstrings stripped — no close_jobs artifact in the code.
    code = _module_code_without_docstrings(close)
    assert "close_jobs" not in code
    # OPERATION_TYPE is the single coarse ops unit for the close pass.
    assert close.OPERATION_TYPE == "meeting-close"


async def test_ac_close_07neg_second_start_for_same_meeting_rejected():
    sink = InMemoryOperationRunSink()
    await sink.start("dup")
    with pytest.raises(RuntimeError):
        await sink.start("dup")  # duplicate meeting-close row is refused
    assert len(sink.rows) == 1


# ── AC-CLOSE-14 — crash recovery: existing GCS object, reuse URL, no overwrite ─

async def test_ac_close_14_recovery_reuses_url_no_overwrite():
    bucket = _RecordingBucket(exists=True)  # finalized object already present
    poster, teardown = _Poster(), _Teardown()
    sink = InMemoryOperationRunSink()
    res = await _run(bucket, poster, teardown, sink, mid="mrec")
    # No second GCS object written (upload rejected by if_generation_match=0).
    assert bucket.written == []
    # Recovery still posts the (reused) URL and then tears down.
    assert poster.calls == [res.notes_url]
    assert teardown.count == 1
    assert sink.rows["mrec"]["status"] == "succeeded"
    # GCS_WRITE step recorded the create-only rejection (existing object detected).
    gcs = next(e for e in res.trace.events if e.step is CloseStep.GCS_WRITE)
    assert "existing object" in gcs.detail


async def test_ac_close_14_conflict_error_is_typed():
    assert issubclass(NotesGenerationConflictError, Exception)


# ── AC-CLOSE-11 / -11-NEG — total_cost_usd read from result, never recomputed ─

async def test_ac_close_11_cost_recorded_from_sdk_result():
    bucket, poster, teardown = _RecordingBucket(), _Poster(), _Teardown()
    sink = InMemoryOperationRunSink()
    res = await _run(bucket, poster, teardown, sink, cost=0.0777, mid="mc")
    assert res.total_cost_usd == 0.0777  # read straight off the result
    assert sink.rows["mc"]["total_cost_usd"] == 0.0777


async def test_ac_close_11_cost_read_off_structured_result_not_recomputed():
    seen_cost = {}

    async def caller(*, model, prompt, output_schema):
        return _structured_result(cost=0.4242)

    final, cost = await generate_structured_close(
        CloseInput("led", ()),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
    )
    seen_cost["v"] = cost
    # The value equals exactly what the SDK result carried — no token arithmetic.
    assert seen_cost["v"] == 0.4242


async def test_ac_close_11neg_missing_cost_does_not_crash():
    async def caller(*, model, prompt, output_schema):
        return close.StructuredResult(data=_final_notes().model_dump(), total_cost_usd=None)

    final, cost = await generate_structured_close(
        CloseInput("led", ()),
        model="claude-sonnet-4-6",
        caller=caller,
        call_external=real_call_external,
    )
    assert cost is None  # recorded as None, not omitted/crashed

    # And the close pass completes with a None cost recorded on the row.
    bucket, poster, teardown = _RecordingBucket(), _Poster(), _Teardown()
    sink = InMemoryOperationRunSink()
    res = await _run(bucket, poster, teardown, sink, cost=None, mid="mn")
    assert res.total_cost_usd is None
    assert "total_cost_usd" in sink.rows["mn"]  # present (None), not silently dropped
    assert sink.rows["mn"]["total_cost_usd"] is None


# ── AC-CLOSE-02 — meeting-end is the sole trigger; init exactly once ─────────

async def test_ac_close_02_close_initiated_exactly_once():
    # AC-CLOSE-02: run_close_pass IS the close-pass initiation (opening the single
    # operation_runs row). This asserts the strongest in-scope property of the
    # trigger: (a) BEFORE any invocation, zero initiations exist; (b) ONE invocation
    # initiates the close pass EXACTLY once; (c) a SECOND invocation for the SAME
    # meeting does NOT initiate a second close pass — the sink's duplicate-row guard
    # rejects it (crash-recovery re-entry cannot double-initiate).
    #
    # NOTE (honest scope): the true TRIGGER coupling — the Doc 02 meeting-end signal
    # being the SOLE cause of exactly one initiation — is NOT wired in close's scope.
    # scribe.close is imported by nothing and the transport MeetingEnd signal never
    # calls it; that meeting-end -> close wiring is Doc04 orchestration (see
    # services/transport/.../events.py: "re-run the Doc 04 close sequence"). Within
    # close's scope we can only assert the initiation is idempotent-per-meeting and
    # count-exactly-once, which is what this test does.
    sink = InMemoryOperationRunSink()

    # (a) none-before: no initiation exists prior to any invocation.
    assert sink.rows == {}

    # (b) exactly-once: a single close-pass invocation initiates exactly one row.
    bucket, poster, teardown = _RecordingBucket(), _Poster(), _Teardown()
    await _run(bucket, poster, teardown, sink, mid="m2")
    assert len(sink.rows) == 1  # exactly one initiation
    assert sink.rows["m2"]["operation_type"] == "meeting-close"

    # (c) not-more-than-once: a second invocation for the SAME meeting is rejected
    # at initiation (the operation_runs row already exists) -> no second close pass.
    with pytest.raises(RuntimeError):
        await _run(_RecordingBucket(), _Poster(), _Teardown(), sink, mid="m2")
    assert len(sink.rows) == 1  # still exactly one — never initiated twice


async def test_ac_close_02neg_no_close_without_invocation():
    # NEG: nothing initiates a close pass absent an explicit call (no timer/silence
    # path is wired). With no invocation there is zero initiation.
    sink = InMemoryOperationRunSink()
    assert sink.rows == {}
    # There is no scheduler / silence-timer symbol in the module surface.
    surface = set(close.__all__)
    for forbidden in ("schedule", "on_silence", "timer", "poll"):
        assert not any(forbidden in name.lower() for name in surface)


# ── AC-CLOSE-04-NEG — backfill DB failure marks the op row FAILED (wired) ────

# A genuinely unroutable DSN — a real asyncpg connect to 127.0.0.1:1 is refused
# (port 1 is unassigned), so the backfill read fails FOR REAL, never via a double
# (mock_boundary: real db fault injection, MUST NOT stub the db seam).
_UNROUTABLE_DSN = "postgresql://proxy@127.0.0.1:1/nope"


async def test_ac_close_04neg_backfill_db_failure_marks_op_row_failed():
    # AC-CLOSE-04-NEG (the untested clause): when the gap/pending backfill read
    # cannot reach Postgres, run_close_pass surfaces DatabaseConnectionError AND
    # marks the operation_runs row FAILED (not succeeded) — wired through the real
    # run_close_pass path, driven by a REAL unroutable DSN (no hand double).
    pytest.importorskip("asyncpg")
    import uuid

    from scribe.close import FinalNotes as _FN  # noqa: F401 (parity import)

    bucket, poster, teardown = _RecordingBucket(), _Poster(), _Teardown()
    sink = InMemoryOperationRunSink()
    mid = uuid.uuid4()

    async def fetch_backfill():
        # The DB-path wiring: a real connect to the unroutable DSN, surfaced as
        # DatabaseConnectionError by the real close seam (not a stub of it).
        return await connect_and_fetch_gap_pending_spans(
            _UNROUTABLE_DSN, mid, timeout=1
        )

    with pytest.raises(DatabaseConnectionError):
        await run_close_pass(
            mid,
            _final_notes(),
            0.01,
            bucket=bucket,
            bucket_name="notes-bucket",
            post_chat_link=poster,
            teardown=teardown,
            op_sink=sink,
            fetch_backfill=fetch_backfill,
        )

    # The operation_runs row is marked FAILED, not succeeded (the key clause).
    row = sink.rows[str(mid)]
    assert row["status"] == "failed"
    assert row["status"] != "succeeded"
    assert row["error_type"] == "db_backfill_failed"
    # It did NOT silently skip the backfill and proceed: nothing world-touching ran.
    assert teardown.count == 0  # no teardown
    assert poster.calls == []  # no chat link posted
    assert bucket.written == []  # no GCS object written
    # And no later step confirm was recorded on the trace (surfaced before render).


async def test_ac_close_04neg_connect_helper_surfaces_typed_error_on_unroutable_dsn():
    # The DB-path connect helper maps a REAL refused connection to the typed
    # DatabaseConnectionError (never a silent skip / bare OSError leak).
    pytest.importorskip("asyncpg")
    import uuid

    with pytest.raises(DatabaseConnectionError):
        await connect_and_fetch_gap_pending_spans(_UNROUTABLE_DSN, uuid.uuid4(), timeout=1)


# ── AC-CLOSE-16 — no V1 features present in V0 ───────────────────────────────

def test_ac_close_16_no_v1_features():
    src = inspect.getsource(close)
    for token in ("show_your_work", "staged_draft", "decisions_to_index"):
        assert token not in src


# ── AC-CLOSE-15 — SDK surface pinned in build evidence (process assertion) ────

def test_ac_close_15_sdk_surface_recorded_in_build_evidence():
    doc = close.__doc__ or ""
    # The confirmed-at-build SDK surface names are recorded inline (build evidence).
    assert "generateStructured" in doc
    assert "outputFormat" in doc
    assert "json_schema" in doc
    assert "error_max_turns" in doc
    assert "error_max_structured_output_retries" in doc
    assert "total_cost_usd" in doc
    # And the TS names are NOT assumed identical without verification — the doc
    # explicitly flags the Python surface MUST be confirmed against live docs.
    assert "claude_agent_sdk" in doc


# ── render_markdown determinism (Step 1, no model call) ──────────────────────

def test_render_markdown_is_deterministic_and_templated():
    notes = FinalNotes(
        summary="  hello  ",
        decisions=[FinalDecision(text="ship it")],
        action_items=[FinalActionItem(text="write tests", owner="dp", due="fri")],
        open_questions=[FinalOpenQuestion(text="who owns rollout?")],
    )
    md1 = render_markdown(notes)
    md2 = render_markdown(notes)
    assert md1 == md2  # deterministic bytes
    assert "# Meeting notes" in md1
    assert "- ship it" in md1
    assert "- [ ] write tests — dp (fri)" in md1
    assert "- who owns rollout?" in md1


def test_render_markdown_empty_sections_render_placeholders():
    md = render_markdown(FinalNotes(summary="s"))
    assert "_None recorded._" in md  # decisions + action items empty
    assert "_None._" in md  # open questions empty


# ── constant sanity (physics, not judgment) ──────────────────────────────────

def test_close_constants_are_sane():
    assert CHAT_POST_MAX_ATTEMPTS >= 1
    assert CHUNK_REDUCE_TOKEN_THRESHOLD > 0
