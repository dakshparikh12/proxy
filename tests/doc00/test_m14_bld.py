"""Doc 00 · §16 Build order & the pre-build validation spike (AC-BLD-001..003).

Milestone m14. The spike is founder-present and runs on REAL infra + REAL repos
(rung-2 territory); this evidence layer asserts the spike's GATE LOGIC, its
deterministic FALLBACK selection, and the step-1 provable bundle — the parts that
are deterministically checkable — using the mechanically-derived blast-radius
golden (goldens/spike_blast_radius.json) as the branch-(b) answer key.

Product imports live INSIDE test bodies, so this collects clean and fails red
before the spike harness exists.
"""

import statistics

import pytest

import _support as S


# ── AC-BLD-001 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_bld_001_spike_gates_latency_and_orm_before_substrate():
    """AC-BLD-001: pre-build spike gates on direct-answer p50<=~2.5s and reliable who_writes/get_dependents before substrate."""
    from spike import gate  # red before the product's spike harness exists

    # Branch (a): direct-answer p50 gate at <= ~2.5s over ~10 real questions.
    fast = [1.2, 1.4, 1.1, 1.9, 2.0, 1.3, 1.7, 1.5, 1.6, 1.8]
    slow = [2.9, 3.4, 3.1, 2.7, 3.8, 3.0, 2.8, 3.3, 3.6, 3.2]
    assert statistics.median(fast) <= 2.5 and statistics.median(slow) > 2.5  # test-side sanity
    assert gate.direct_answer_p50_gate(fast) is True, "p50<=2.5s must PASS branch (a)"
    assert gate.direct_answer_p50_gate(slow) is False, "p50>2.5s must FAIL branch (a) (architecture problem)"

    # Branch (b): get_dependents/who_writes accuracy graded against the mechanical golden.
    golden = S.load_golden("spike_blast_radius.json")
    truth = {d["module"] for d in golden["direct_importers"]}

    good = gate.orm_accuracy_gate(measured_direct=truth, golden_direct=truth)
    assert good.passed is True, "an answer matching the golden must pass branch (b)"

    # A degraded answer that drops a real dependent must FAIL and ESCALATE (challenge-core),
    # never be silently patched to pass.
    degraded = gate.orm_accuracy_gate(measured_direct=set(), golden_direct=truth)
    assert degraded.passed is False, "missing a real dependent must fail branch (b)"
    assert degraded.escalate_as_challenge_core is True, "an ORM-accuracy fail escalates to the user, not a silent patch"

    # The substrate is not committed until BOTH gates hold (or a fallback is applied).
    assert gate.substrate_commit_allowed(branch_a=True, branch_b=True) is True
    assert gate.substrate_commit_allowed(branch_a=True, branch_b=False) is False, (
        "substrate must NOT be committed while a spike gate is unmet"
    )


# ── AC-BLD-002 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_bld_002_failing_branch_applies_deterministic_fallback():
    """AC-BLD-002: a failing spike branch applies its deterministic fallback, never a silent patch."""
    from spike import gate

    cases = {
        "latency-fail": "shallow-only + ACK-tile",
        "orm-fail": "restrict who_writes matrix + label lower-bound",
        "sdk-shape-differs": "write an adapter or fail the build",
    }
    for failure, expected in cases.items():
        fb = gate.select_fallback(failure)
        # The named fallback is selected (match on its salient tokens, not exact prose).
        norm = str(fb).lower()
        for token in _salient_tokens(expected):
            assert token in norm, f"{failure} must map to its named fallback ({expected}); got {fb!r}"
        assert "silent" not in norm and "patch to pass" not in norm, f"{failure} must never be silently patched"

    # An unknown/ad-hoc failure must NOT resolve to a silent pass-through patch.
    with pytest.raises(Exception):
        gate.select_fallback("just-make-it-green")


def _salient_tokens(phrase: str) -> list[str]:
    mapping = {
        "shallow-only + ACK-tile": ["shallow", "ack"],
        "restrict who_writes matrix + label lower-bound": ["restrict", "lower-bound"],
        "write an adapter or fail the build": ["adapter"],
    }
    return mapping[phrase]


# ── AC-BLD-003 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_bld_003_step1_provable_bundle_complete():
    """AC-BLD-003: step-1 completion proves CI-green, self-migrate+/health, deploy-lands, registry-closed, harness heartbeat+self-reap."""
    from spike import step1  # or the product's step-1 provable-bundle assembler

    provables = step1.gather_provables()
    required = {
        "ci_green",
        "container_self_migrates_and_serves_health",
        "deploy_lands",
        "registry_closed",
        "harness_operation_run_heartbeats_then_self_reaps",
    }
    assert required <= set(provables), f"step-1 provable bundle missing facts: {required - set(provables)}"
    unmet = [k for k in required if not provables[k]]
    assert unmet == [], f"step-1 is not done until every provable holds; unmet: {unmet}"
