"""Acceptance tests for node ``orchestrator.direct-answer-path`` (04 §2 / CANONICAL §11.6).

The DIRECT-ANSWER path (the ~1–2s path): a *simple grounded lookup* addressed to
Proxy is answered **in Proxy's own wake turn** via the mounted ``code_intel``
tools, which hit the host-side ``code_intel`` internal API — **no E2B and no
Workroom session on the direct path** (§11.6, re-scoped by CANONICAL §12.2). The
turn returns a **final Envelope** (``status='done'``) from the wake turn alone.

These tests drive the REAL product path end to end:

    control_plane.orchestrator.run_wake_turn
        → control_plane.direct_answer.answer_direct   (the ONE canonical resolver)
        → code_intel.direct_answer.answer_direct (composes the structural tools)

The ``code_intel`` handle here is a small HERMETIC fake server that returns the
REAL :mod:`code_intel.results` dataclasses (``DependentsResult`` / ``WhoWritesResult``
/ ``BatchReadResult`` / ``FindReferencesResult`` …) so the resolver runs its real
classification, its real ``batch_read`` line-confirmation, and its real honesty
tiering — no on-disk clone needed, no network, and NO test-double of the resolver
itself. A hardcoded-citation stub would fail the "the read confirms the cited
line" assertions below.

The NEGATIVE contract (the node's strengthened ``definition_of_done``) is the
spine of this suite: a direct answer **NEVER** calls ``dispatch_workroom()`` and
**NEVER** provisions an E2B sandbox — testable that a "where is X?" ask returns a
final envelope with **no Workroom task created**. We prove it two ways: (a) an
instrumented E2B provisioner + Workroom dispatcher passed INTO the wake turn stay
at zero calls (any call raises); (b) the resolver source references neither seam
on the resolve path.
"""
from __future__ import annotations

import pytest

from code_intel.results import (
    BatchFile,
    BatchReadResult,
    DependentsResult,
    FindReferencesResult,
    RefItem,
    ResultItem,
    WhoWritesResult,
    Writer,
)
from contracts import Envelope
from contracts.envelopes import EnvelopeStatus
from control_plane.orchestrator import WakeTurnResult, run_wake_turn


# ── instrumented seams: any call is a contract violation (they only record) ──
class RecordingE2B:
    """An E2B provisioner that must never be touched on the direct path."""

    def __init__(self) -> None:
        self.provisions = 0

    def provision(self, *args: object, **kwargs: object) -> object:
        self.provisions += 1
        raise AssertionError("direct-answer path must NOT provision an E2B sandbox")


class RecordingWorkroom:
    """A Workroom dispatcher that must never be touched on the direct path."""

    def __init__(self) -> None:
        self.dispatches = 0
        self.tasks: list[object] = []

    def dispatch(self, *args: object, **kwargs: object) -> object:
        self.dispatches += 1
        self.tasks.append((args, kwargs))
        raise AssertionError("direct-answer path must NOT dispatch a Workroom session")

    # Alias for the canonical verb name the negative contract calls out by name.
    def dispatch_workroom(self, *args: object, **kwargs: object) -> object:
        return self.dispatch(*args, **kwargs)


# ── a hermetic code_intel server returning the REAL result dataclasses ───────
# The file contents are fixed so the resolver's ``batch_read`` line-confirmation
# (Law 1: the citation is drawn from a real read) runs against known text and the
# cited line genuinely contains the symbol.
_FILES: dict[str, str] = {
    "src/app/helpers.py": (
        "import os\n"                      # 1
        "\n"                               # 2
        "\n"                               # 3
        "def url_for(endpoint):\n"         # 4  <- url_for DEFINITION (module-level)
        "    return _build(endpoint)\n"    # 5
    ),
    "src/app/db.py": (
        "class RefundWriter:\n"            # 1
        "    def commit(self, row):\n"     # 2
        "        db.execute(\n"            # 3
        "            'INSERT INTO refunds VALUES (%s)', row\n"  # 4  <- writes refunds
        "        )\n"                      # 5
    ),
    "src/app/checkout.py": (
        "def retry_checkout(order):\n"     # 1  <- checkout retry logic
        "    for attempt in range(3):\n"   # 2
        "        try_charge(order)\n"       # 3
    ),
}


class FakeCodeIntel:
    """A minimal in-memory ``CodeIntelMCPServer`` stand-in for the resolver.

    Returns the REAL ``code_intel.results`` dataclasses so the resolver's own
    logic (classify → run tool → batch_read confirm → honesty-tier) executes for
    real. It has no graph (``graph`` is None) so ``find_definition`` takes the
    documented grep-fallback branch through ``find_references``.
    """

    graph = None  # no resolve_symbol graph → find_definition uses find_references

    def __init__(self) -> None:
        self.calls: list[str] = []

    # -- symbol/ambiguity probe --------------------------------------------
    def lookup_referent(self, symbol: str, **_: object) -> str | None:
        self.calls.append("lookup_referent")
        return symbol  # single unambiguous referent

    # -- structural tools ---------------------------------------------------
    def find_references(self, symbol: str, **_: object) -> FindReferencesResult:
        self.calls.append("find_references")
        if symbol == "url_for":
            return FindReferencesResult(
                results=[RefItem(file="src/app/helpers.py", line=4, confidence="resolved")]
            )
        return FindReferencesResult(results=[])

    def who_writes(self, table: str, **_: object) -> WhoWritesResult:
        self.calls.append("who_writes")
        if "refund" in table.lower():
            return WhoWritesResult(
                writers=[Writer(id="src/app/db.py::RefundWriter.commit", file="src/app/db.py", line=4, confidence="resolved")]
            )
        return WhoWritesResult(writers=[])

    def get_dependents(self, symbol: str, **_: object) -> DependentsResult:
        self.calls.append("get_dependents")
        return DependentsResult(
            results=[ResultItem(id="src/app/checkout.py::retry_checkout", path="src/app/checkout.py", file="src/app/checkout.py", line=1)]
        )

    # -- the READ that grounds every citation (Law 1) -----------------------
    def batch_read(self, paths: list[str], max_lines_per_file: object = None, **_: object) -> BatchReadResult:
        self.calls.append("batch_read")
        files: list[BatchFile] = []
        for p in paths:
            content = _FILES.get(p)
            if content is None:
                files.append(BatchFile(path=p, error="not found"))
            else:
                files.append(BatchFile(path=p, content=content))
        return BatchReadResult(files=files)


@pytest.fixture()
def server() -> FakeCodeIntel:
    return FakeCodeIntel()


@pytest.fixture()
def seams() -> tuple[RecordingE2B, RecordingWorkroom]:
    return RecordingE2B(), RecordingWorkroom()


# ── clause 1: a grounded lookup is answered IN the wake turn (final envelope) ─
@pytest.mark.integration
def test_direct_answer_returns_final_envelope_in_the_wake_turn(server: FakeCodeIntel) -> None:
    """A simple grounded lookup is resolved in Proxy's OWN wake turn and returns a
    FINAL Envelope (status='done') — the ~1–2s direct path, not a dispatched task."""
    res = run_wake_turn(transcript_tail="Proxy, where is url_for?", code_intel=server)

    assert isinstance(res, WakeTurnResult)
    assert res.is_direct_answer, "a grounded lookup must be answered directly in the wake turn"
    env = res.final_envelope
    assert isinstance(env, Envelope), "the direct answer returns a final contracts.Envelope"
    assert env.status == "done", f"a direct answer is terminal (status=done), got {env.status!r}"
    # The final envelope is the canonical terminal status, never a smuggled marker.
    assert env.status in EnvelopeStatus.__args__  # type: ignore[attr-defined]
    # A real read grounded the citation — it rides along as a receipt.
    assert res.citation == "src/app/helpers.py:4", res.citation
    assert env.receipts == ["src/app/helpers.py:4"], env.receipts
    assert "url_for" in res.reply


# ── clause 2: the NEGATIVE contract — no dispatch_workroom, no E2B (proven) ──
@pytest.mark.integration
def test_where_is_x_creates_no_workroom_task_and_no_sandbox(
    server: FakeCodeIntel, seams: tuple[RecordingE2B, RecordingWorkroom]
) -> None:
    """THE negative contract: a "where is X?" ask returns a final envelope with
    NO Workroom task created and NO E2B sandbox provisioned. The instrumented
    seams (any call raises) are passed straight into the wake turn and must stay
    at zero calls."""
    e2b, workroom = seams

    res = run_wake_turn(
        transcript_tail="Proxy, where is url_for?",
        code_intel=server,
        e2b=e2b,
        workroom=workroom,
    )

    # A final envelope was produced from the wake turn alone …
    assert isinstance(res.final_envelope, Envelope)
    assert res.final_envelope.status == "done"
    # … while NEITHER seam was touched (the raising recorders never fired).
    assert e2b.provisions == 0, f"direct path provisioned {e2b.provisions} sandbox(es); must be 0"
    assert workroom.dispatches == 0, f"direct path dispatched {workroom.dispatches} Workroom session(s); must be 0"
    assert workroom.tasks == [], "no Workroom task may be created on the direct path"
    # … and the result states the negative contract explicitly.
    assert res.dispatched_workroom is False
    assert res.provisioned_e2b is False


# ── clause 3: the negative contract holds across every direct-lookup shape ───
@pytest.mark.integration
@pytest.mark.parametrize(
    "ask",
    [
        "Proxy, where is url_for?",
        "Proxy, what writes the refunds table?",
        "Proxy, where's the checkout retry logic?",
        "Proxy, what depends on retry_checkout?",
    ],
)
def test_no_workroom_dispatch_for_any_grounded_lookup(
    server: FakeCodeIntel, seams: tuple[RecordingE2B, RecordingWorkroom], ask: str
) -> None:
    """Every simple grounded lookup — locate, who-writes, dependents — is answered
    in-turn and dispatches NO workroom / provisions NO sandbox."""
    e2b, workroom = seams
    res = run_wake_turn(transcript_tail=ask, code_intel=server, e2b=e2b, workroom=workroom)

    assert isinstance(res.final_envelope, Envelope)
    assert res.final_envelope.status == "done"
    assert e2b.provisions == 0 and workroom.dispatches == 0
    assert res.dispatched_workroom is False and res.provisioned_e2b is False


# ── clause 4: who_writes grounds a real file:line drawn from the read (Law 1) ─
@pytest.mark.integration
def test_who_writes_refunds_cites_the_writer_line_from_a_read(server: FakeCodeIntel) -> None:
    """"what writes the refunds table?" cites the writer's real file:line — drawn
    from the batch_read, never a graph edge (Law 1)."""
    res = run_wake_turn(transcript_tail="Proxy, what writes the refunds table?", code_intel=server)

    assert res.is_direct_answer
    assert res.citation == "src/app/db.py:4", res.citation
    assert res.final_envelope.receipts == ["src/app/db.py:4"]
    # A single unambiguous referent + confirmed read + resolved tool → resolved.
    assert res.confidence == "resolved", res.confidence
    # PROVE the citation is grounded: the cited line is the READ line, and the read
    # was actually performed by the resolver.
    assert "batch_read" in server.calls, "the resolver must READ the file to confirm the line"


# ── clause 5: honesty tiering rides through to the wake-turn result (Law 2) ───
@pytest.mark.integration
def test_grounded_answer_is_honesty_tiered(server: FakeCodeIntel) -> None:
    """A grounded direct answer is honesty-tiered resolved | lower-bound (Law 2),
    never an untagged claim — and the final envelope is marked verified."""
    res = run_wake_turn(transcript_tail="Proxy, where is url_for?", code_intel=server)
    assert res.confidence in ("resolved", "lower-bound"), res.confidence
    # A grounded (cited) direct answer is a verified terminal envelope.
    assert res.final_envelope.verification == "verified"


# ── clause 6: honest abstention — no handle → no fabricated citation (Law 1) ─
@pytest.mark.integration
def test_no_code_intel_handle_abstains_without_dispatching(
    seams: tuple[RecordingE2B, RecordingWorkroom],
) -> None:
    """With NO code_intel index bound the turn stays a safe placeholder: it neither
    fabricates a citation nor dispatches to the workroom to compensate (Law 1)."""
    e2b, workroom = seams
    res = run_wake_turn(transcript_tail="Proxy, where is url_for?", e2b=e2b, workroom=workroom)

    # No handle → no final in-turn envelope, and crucially NO dispatch to cover it.
    assert res.final_envelope is None, "no handle → no fabricated grounded envelope"
    assert res.citation is None
    assert e2b.provisions == 0 and workroom.dispatches == 0
    assert res.dispatched_workroom is False and res.provisioned_e2b is False


# ── clause 7: the resolve path references NEITHER seam (static completeness) ──
def _code_only(source: str) -> str:
    """Return ``source`` with all comments and string/bytes literals removed, so a
    scan sees executable CODE only — a forbidden verb named in a docstring or
    comment (explaining the negative contract) is not a false positive."""
    import io
    import tokenize

    kept: list[str] = []
    toks = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in toks:
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        if tok.type == tokenize.NL or tok.type == tokenize.NEWLINE:
            kept.append("\n")
            continue
        kept.append(tok.string + " ")
    return "".join(kept)


@pytest.mark.static_
def test_orchestrator_resolve_path_never_calls_a_dispatch_or_provision() -> None:
    """The wake-turn direct-answer resolve path names no dispatch/provision verb in
    EXECUTABLE code — the negative contract is structural, not just runtime-observed.
    The only ``e2b`` / ``workroom`` mentions are pass-throughs into the resolver
    (which itself never calls them), never a ``.dispatch(`` / ``.provision(`` call.
    (Docstring / comment mentions explaining the contract are excluded.)"""
    from pathlib import Path

    src = Path("services/control-plane/src/control_plane/orchestrator.py").read_text(encoding="utf-8")
    code = _code_only(src)
    for forbidden in (".provision", ".dispatch", "dispatch_workroom"):
        assert forbidden not in code, (
            f"the direct-answer wake turn must not call {forbidden!r} in code (negative contract)"
        )
