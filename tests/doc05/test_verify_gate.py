"""Doc 05 · workroom.verify-gate — the fresh-context critic + deterministic evidence gate
+ the hard gate that stamps verification (§3.7 ①-③).

Node ``workroom.verify-gate`` (evidence class ``[negative]``). The DoD, made executable
against the REAL host path with in-process fakes (e2b NOT installed; the E2B-template bake is
the flagged Phase-3 residual, never faked here):

  * ① a SEPARATE critic in FRESH context (a NEW ``query()``, builder's success log WITHHELD —
    anti-anchoring) that CATCHES a planted wrong claim and downgrades verification. Fail-closed:
    an unparseable / uncertain verdict → ``unverified`` (never ``verified``); on total parse
    failure every criterion defaults FAILED. The verifier is READ-ONLY — it never advertises a
    write/edit/propose_change tool (a verifier never edits the artifact it grades, §3.7).
  * ② the deterministic evidence gate (~30 lines, non-LLM) — ``evidence_backed()`` reads the
    HOST-observed receipts (``{command_id, argv, exit_code, stdout_ref, artifact_hashes}``, §3.5)
    and NEVER the model's prose. A claimed pass is real ONLY if a receipt shows the named verify
    command ran at ``exit_code == 0`` (and any required artifact hashes match). No matching
    receipt → force-FAIL.
  * ③ the hard gate stamps ``verification='verified'`` ONLY when the critic AND the evidence gate
    both pass (status ``done``); else ``unverified`` + staged as a draft → ``needs_review``. The
    builder NEVER grades its own work.

THE two DoD proofs (both negative):
  * plant a LYING pass with NO exit-0 receipt → the deterministic gate FORCE-FAILS it (a claimed
    pass without a real host-observed exit-0 receipt can NEVER reach ``verified``);
  * plant a WRONG claim → the fresh critic catches it and downgrades verification to ``unverified``.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from contracts import AgentChunk, Bundle

from workroom.verify_gate import (
    VerifyGate,
    VerifyGateResult,
    Verdict,
    evidence_backed,
)


# ── the host-observed receipt shape the transport emits (§3.5 / D-017) ────────
# {command_id, argv, exit_code, stdout_ref, artifact_hashes:[{path, sha256}]}. The gate reads
# THESE, never the model's prose.
def _receipt(
    argv: list[str],
    exit_code: int,
    *,
    artifact_hashes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "command_id": uuid4().hex,
        "argv": list(argv),
        "exit_code": int(exit_code),
        "stdout_ref": "sha256:some-real-captured-stream",
        "artifact_hashes": list(artifact_hashes or []),
    }


def _bundle(ask: str = "build the per-user rate-limiter") -> Bundle:
    return Bundle(
        ask=ask,
        speaker="Sam",
        timestamp=datetime.now(UTC),
        notes_ref=uuid4(),
        transcript_tail="...we discussed a token bucket",
        task_id=uuid4(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# The fresh-context critic provider (a NEW query() — the builder never grades itself)
# ═══════════════════════════════════════════════════════════════════════════
class FakeVerifierProvider:
    """A fresh-context VERIFIER ``query()`` that emits a schema-constrained verdict (§3.7①).

    It records the ``system_prompt`` / ``allowed_tools`` / prompt it saw so a test can prove the
    verifier is a SEPARATE turn, is READ-ONLY (no write tools), and NEVER received the builder's
    success log (anti-anchoring). ``verdict`` is the JSON object it returns as its final TEXT;
    ``bad_json`` makes it emit unparseable prose (→ fail-closed to unverified / all-FAILED);
    ``error`` makes it emit a terminal ERROR chunk (a verifier fault → fail-closed).
    """

    name = "claude"

    def __init__(
        self,
        *,
        verdict: dict[str, Any] | None = None,
        bad_json: bool = False,
        error: bool = False,
    ) -> None:
        self._verdict = verdict
        self._bad_json = bad_json
        self._error = error
        self.calls = 0
        self.seen_prompts: list[str] = []
        self.seen_system_prompts: list[str] = []
        self.seen_allowed_tools: list[tuple[str, ...]] = []
        self.seen_models: list[str] = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        self.seen_prompts.append(prompt)
        self.seen_system_prompts.append(getattr(query, "system_prompt", "") or "")
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        self.seen_models.append(str(getattr(query, "model", "")))
        error = self._error
        bad = self._bad_json
        verdict = self._verdict

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": f"verifier-sess-{self.calls}"})
            if error:
                yield AgentChunk(type="ERROR", metadata={"message": "verifier provider blew up"})
                return
            if bad:
                yield AgentChunk(
                    type="TEXT",
                    text="I looked and, uh, it all seems fine? Passed I think.",
                    metadata={"msg_id": "v1"},
                )
            else:
                yield AgentChunk(type="TEXT", text=json.dumps(verdict), metadata={"msg_id": "v1"})
            yield AgentChunk(
                type="RESULT",
                metadata={"session_id": f"verifier-sess-{self.calls}", "total_cost_usd": 0.05},
            )

        return gen()


# A verdict where the critic found EVERY AC met, the artifact runs, claims grounded, in scope.
_ALL_GREEN_VERDICT: dict[str, Any] = {
    "criteria": [
        {"ac": "AC1", "met": True, "evidence": "ratelimit.py:12 token bucket; re-ran pytest exit 0"},
        {"ac": "AC2", "met": True, "evidence": "routes.py:88 returns 429; charge_test exit 0"},
    ],
    "runs": True,
    "grounded": True,
    "in_scope": True,
}

# A verdict where the critic CAUGHT a wrong claim: AC2 is NOT met (the builder claimed it was).
_CRITIC_CAUGHT_WRONG_CLAIM: dict[str, Any] = {
    "criteria": [
        {"ac": "AC1", "met": True, "evidence": "ratelimit.py:12 token bucket; re-ran pytest exit 0"},
        {
            "ac": "AC2",
            "met": False,
            "evidence": "routes.py:88 returns 200, NOT 429 on limit — the claim is wrong",
        },
    ],
    "runs": True,
    "grounded": True,
    "in_scope": True,
}


# ═══════════════════════════════════════════════════════════════════════════
# ② The deterministic evidence gate — reads receipts, NEVER model prose
# ═══════════════════════════════════════════════════════════════════════════
def test_evidence_backed_true_when_named_command_ran_exit0() -> None:
    """A named verify command with a host-observed exit-0 receipt IS evidence-backed (§3.7②)."""
    cmds = ["pytest tests/ratelimit_test.py"]
    receipts = [_receipt(["pytest", "tests/ratelimit_test.py"], 0)]
    assert evidence_backed(cmds, receipts) is True


def test_evidence_backed_false_when_receipt_exit_nonzero() -> None:
    """A receipt whose REAL exit_code is non-zero can NEVER back a pass (§3.7②)."""
    cmds = ["pytest tests/ratelimit_test.py"]
    receipts = [_receipt(["pytest", "tests/ratelimit_test.py"], 1)]
    assert evidence_backed(cmds, receipts) is False


def test_evidence_backed_false_when_no_matching_receipt() -> None:
    """A claimed pass with NO receipt for the named command → not backed (force-FAIL, §3.7②)."""
    cmds = ["pytest tests/ratelimit_test.py"]
    # A receipt exists, but for a DIFFERENT command (an `ls`, not the verify command).
    receipts = [_receipt(["ls", "-la"], 0)]
    assert evidence_backed(cmds, receipts) is False


def test_evidence_backed_false_when_no_verify_cmds() -> None:
    """No named verify command → nothing to prove → NOT backed (fail-closed, §3.7②).

    A build with no machine-checkable verify line cannot be 'verified' — there is no receipt to
    read, so the gate refuses rather than passing on an empty claim."""
    assert evidence_backed([], [_receipt(["pytest", "x"], 0)]) is False


def test_evidence_backed_ignores_model_prose_in_stdout() -> None:
    """THE gate reads receipts, NEVER prose: a receipt whose captured stream SAYS 'all passed'
    but whose REAL exit_code is 1 does NOT back a pass (§3.7② / CANONICAL §12.4)."""
    cmds = ["pytest tests/ratelimit_test.py"]
    # The captured stream (stdout_ref) narration is irrelevant; only the real exit_code counts.
    receipts = [_receipt(["pytest", "tests/ratelimit_test.py"], 1)]
    assert evidence_backed(cmds, receipts) is False


def test_evidence_backed_requires_artifact_hashes_to_match() -> None:
    """When the plan's verify line requires artifact hashes, a receipt whose HOST-computed hash
    differs does NOT back the pass (§3.7② — hashes from the transport, not claimed)."""
    cmds = ["pytest tests/ratelimit_test.py"]
    receipts = [
        _receipt(
            ["pytest", "tests/ratelimit_test.py"],
            0,
            artifact_hashes=[{"path": "lib/ratelimit.py", "sha256": "deadbeef"}],
        )
    ]
    assert evidence_backed(cmds, receipts, {"lib/ratelimit.py": "deadbeef"}) is True
    assert evidence_backed(cmds, receipts, {"lib/ratelimit.py": "WRONGHASH"}) is False


# ═══════════════════════════════════════════════════════════════════════════
# ①+③ The fresh-context critic + hard gate — the DoD proofs (both negative)
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_lying_pass_with_no_exit0_receipt_is_force_failed() -> None:
    """DoD PROOF 1 (deterministic gate): a builder CLAIMS 'passed', the fresh critic even agrees
    every AC is met — but there is NO host-observed exit-0 receipt for the named verify command.
    The deterministic gate FORCE-FAILS it: ``verification`` is ``unverified`` (NOT ``verified``)
    and the result is force-failed. A claimed pass without a real exit-0 receipt can NEVER reach
    'verified' (the node's hard NOT-done)."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(verdict=_ALL_GREEN_VERDICT)  # the critic is fooled too
    result = await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",  # the builder's lying claim (model text — never trusted alone)
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["ls", "-la"], 0)],  # NO receipt for the named verify command
        builder_success_log="U1 ✅ U2 ✅ everything passed, all green!",
        verifier_provider=verifier,
    )
    assert isinstance(result, VerifyGateResult)
    # The evidence gate force-failed it — even though the critic said all-green.
    assert result.evidence_backed is False
    assert result.verification == "unverified", "no exit-0 receipt → NEVER 'verified'"
    assert result.verified is False
    assert result.status == "failed"
    # The downgrade reason is explicit + names the missing host-observed receipt (Law 2).
    assert any("receipt" in r.lower() for r in result.receipts)


@pytest.mark.asyncio
async def test_wrong_claim_is_caught_by_fresh_critic_and_downgrades() -> None:
    """DoD PROOF 2 (fresh critic): the deterministic gate is satisfied (a real exit-0 receipt
    backs the named command), but the builder's claim is WRONG on AC2. The fresh-context critic
    (a SEPARATE query()) catches it → a criterion FAILED → verification downgraded to
    ``unverified`` (staged, ``needs_review``), NOT ``verified``. The builder never grades itself."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(verdict=_CRITIC_CAUGHT_WRONG_CLAIM)
    result = await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["pytest", "tests/ratelimit_test.py"], 0)],  # a REAL exit-0 receipt
        builder_success_log="all green!",
        verifier_provider=verifier,
    )
    # The evidence gate PASSED (a real receipt), but the CRITIC caught the wrong claim.
    assert result.evidence_backed is True
    assert result.critic_all_met is False
    assert result.verification == "unverified", "a failed criterion → NEVER 'verified'"
    assert result.verified is False
    assert result.status == "needs_review", "downgraded + staged for review (§3.7③)"
    assert verifier.calls == 1, "the critic ran as a SEPARATE, fresh-context query()"


@pytest.mark.asyncio
async def test_all_green_and_backed_stamps_verified() -> None:
    """The POSITIVE path: the critic finds every AC met AND a host-observed exit-0 receipt backs
    the named verify command → ``verification='verified'`` + status ``done`` (§3.7③). This is the
    ONLY path to 'verified' — both checks green."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(verdict=_ALL_GREEN_VERDICT)
    result = await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py", "pytest tests/integration/charge_test.py"],
        receipts=[
            _receipt(["pytest", "tests/ratelimit_test.py"], 0),
            _receipt(["pytest", "tests/integration/charge_test.py"], 0),
        ],
        builder_success_log="all green!",
        verifier_provider=verifier,
    )
    assert result.evidence_backed is True
    assert result.critic_all_met is True
    assert result.verified is True
    assert result.verification == "verified"
    assert result.status == "done"


@pytest.mark.asyncio
async def test_builder_success_log_is_withheld_from_the_critic() -> None:
    """Anti-anchoring (§3.7①): the builder's own success log is WITHHELD from the critic prompt —
    it must never leak in and anchor the verdict toward 'passed'. A small, powerful lever."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(verdict=_ALL_GREEN_VERDICT)
    leak_marker = "SECRET-BUILDER-NARRATION-all-green-trust-me"
    await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["pytest", "tests/ratelimit_test.py"], 0)],
        builder_success_log=f"U1 done. {leak_marker}. U2 done. everything passed!",
        verifier_provider=verifier,
    )
    assert verifier.calls == 1
    critic_prompt = verifier.seen_prompts[0]
    assert leak_marker not in critic_prompt, "the builder's success log must NOT leak to the critic"


@pytest.mark.asyncio
async def test_verifier_is_read_only_never_edits_the_artifact_it_grades() -> None:
    """The verifier disposition is READ-ONLY (§3.7): it re-runs tests (run_command) but NEVER
    advertises a write/edit/ast_grep/propose_change tool — a verifier never edits what it grades."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(verdict=_ALL_GREEN_VERDICT)
    await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["pytest", "tests/ratelimit_test.py"], 0)],
        builder_success_log="",
        verifier_provider=verifier,
    )
    allowed = verifier.seen_allowed_tools[0]
    for banned in ("write_file", "edit_file", "ast_grep", "propose_change"):
        assert banned not in allowed, f"the verifier must not advertise the write tool {banned!r}"


@pytest.mark.asyncio
async def test_verifier_runs_on_a_model_at_least_as_strong_as_the_worker() -> None:
    """§3.2: the verifier is 'stronger than the worker, fresh context' (anti-anchoring). The worker
    rides the Opus-class BIG_BUILD seat; the verifier must resolve a model AT LEAST as strong —
    never a literal here, resolved through the imported ``llm.routing`` seat table."""
    from llm.routing import model_for

    worker_model = model_for("BIG_BUILD")
    gate = VerifyGate()
    verifier = FakeVerifierProvider(verdict=_ALL_GREEN_VERDICT)
    await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["pytest", "tests/ratelimit_test.py"], 0)],
        builder_success_log="",
        verifier_provider=verifier,
    )
    seen = verifier.seen_models[0]
    assert seen, "the verifier query() carries a resolved model"
    # The strongest available seat is BIG_BUILD (Opus); the verifier must be at least that.
    assert seen == worker_model, "the verifier is at least as strong as the worker (§3.2)"


@pytest.mark.asyncio
async def test_unparseable_verdict_fails_closed_every_criterion_failed() -> None:
    """Fail-closed (§3.7①): an unparseable / uncertain critic verdict → ``unverified`` (never
    ``verified``); on total parse failure EVERY criterion defaults FAILED. Even though a real
    exit-0 receipt backs the command, the un-gradeable verdict blocks 'verified'."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(bad_json=True)  # prose, not a JSON verdict
    result = await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["pytest", "tests/ratelimit_test.py"], 0)],  # a real exit-0 receipt
        builder_success_log="",
        verifier_provider=verifier,
    )
    assert result.critic_all_met is False, "total parse failure → every criterion FAILED"
    assert result.verification == "unverified"
    assert result.verified is False
    assert result.status == "needs_review"


@pytest.mark.asyncio
async def test_verifier_fault_fails_closed() -> None:
    """A verifier PROVIDER fault (terminal ERROR chunk) is fail-closed (§3.7① / Rule 6): the gate
    never throws across the host boundary and never stamps 'verified' on an un-run verifier."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(error=True)
    result = await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["pytest", "tests/ratelimit_test.py"], 0)],
        builder_success_log="",
        verifier_provider=verifier,
    )
    assert result.critic_all_met is False
    assert result.verification == "unverified"
    assert result.verified is False
    assert result.status == "needs_review"


@pytest.mark.asyncio
async def test_verdict_dataclass_carries_the_criteria() -> None:
    """The critic emits a schema-constrained verdict re-validated on the host (§3.7① — belt +
    suspenders). A parsed verdict exposes the per-criterion met/failed decisions."""
    gate = VerifyGate()
    verifier = FakeVerifierProvider(verdict=_CRITIC_CAUGHT_WRONG_CLAIM)
    result = await gate.verify(
        bundle=_bundle(),
        claimed_status="passed",
        verify_cmds=["pytest tests/ratelimit_test.py"],
        receipts=[_receipt(["pytest", "tests/ratelimit_test.py"], 0)],
        builder_success_log="",
        verifier_provider=verifier,
    )
    assert isinstance(result.verdict, Verdict)
    failed = [c for c in result.verdict.criteria if not c.met]
    assert any(c.ac == "AC2" for c in failed), "the critic recorded AC2 as the failed criterion"


def test_verdict_total_parse_failure_defaults_all_criteria_failed() -> None:
    """Direct proof of the fail-closed parse contract (§3.7①): a totally-unparseable verdict text
    yields a Verdict whose ``all_met`` is False (every criterion defaults FAILED)."""
    v = Verdict.parse("this is not json at all, just prose")
    assert v.all_met is False
    assert v.parsed is False
