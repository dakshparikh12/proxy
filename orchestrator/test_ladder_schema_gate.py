"""Regression tests for ladder_schema_gate.parse_criteria — the inline-flow parse bug that made
doc03's staged bundle falsely fail the ladder gate (2026-07-21).

Root cause: parse_criteria only recognized the BLOCK form of verification_ladder:

    verification_ladder:
      - {tier: lint}
      - {tier: unit}

The doc03 criteria author emitted 161 criteria in the equally-valid INLINE FLOW form:

    verification_ladder: [lint, unit]

which the line-parser silently read as an EMPTY ladder (in_ladder was only set when the
`verification_ladder:` key had nothing after the colon). Result: 161 well-formed criteria were
reported as "MISSING verification_ladder", drowning the ~101 genuinely-missing ones and failing
the seal for the wrong reason. These tests lock in that BOTH forms parse to the same tier set, and
that a genuinely-absent ladder is still reported empty.

Run: .venv/bin/python -m pytest orchestrator/test_ladder_schema_gate.py -q
"""
import importlib.util
import pathlib

ORCH = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ladder_schema_gate", ORCH / "ladder_schema_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _parse(text: str, tmp_path) -> dict:
    """Write text to a temp criteria.yaml, parse it, return {id: criterion-dict}."""
    p = tmp_path / "criteria.yaml"
    p.write_text(text)
    return {c["id"]: c for c in gate.parse_criteria(p)}


# ── 1. the bug: inline flow form must parse to the same tier set as block form ──
def test_inline_flow_ladder_is_parsed(tmp_path):
    """`verification_ladder: [lint, unit]` — valid YAML, semantically identical to the block form."""
    crits = _parse(
        "- criterion_id: AC-COAL-01\n"
        "  dependency_class: null\n"
        "  golden_path: false\n"
        "  verification_ladder: [lint, unit]\n",
        tmp_path,
    )
    assert crits["AC-COAL-01"]["ladder"] == {"lint", "unit"}


def test_inline_flow_with_more_tiers(tmp_path):
    """Inline flow with integration/negative rungs is captured fully, not truncated."""
    crits = _parse(
        "- criterion_id: AC-STORE-01\n"
        "  dependency_class: db:postgres\n"
        "  verification_ladder: [lint, unit, integration]\n"
        "- criterion_id: AC-STORE-01-NEG\n"
        "  dependency_class: db:postgres\n"
        "  verification_ladder: [lint, unit, negative]\n",
        tmp_path,
    )
    assert crits["AC-STORE-01"]["ladder"] == {"lint", "unit", "integration"}
    assert crits["AC-STORE-01-NEG"]["ladder"] == {"lint", "unit", "negative"}


# ── 2. the block form must still parse (no regression) ──
def test_block_form_still_parses(tmp_path):
    crits = _parse(
        "- criterion_id: AC-COAL-02\n"
        "  verification_ladder:\n"
        "    - {tier: lint}\n"
        "    - {tier: unit}\n",
        tmp_path,
    )
    assert crits["AC-COAL-02"]["ladder"] == {"lint", "unit"}


# ── 3. a genuinely-absent ladder is still reported empty (must NOT be masked by the fix) ──
def test_absent_ladder_stays_empty(tmp_path):
    crits = _parse(
        "- criterion_id: AC-EVENT-01\n"
        "  name: \"no ladder fields at all\"\n"
        "  golden_path: false\n",
        tmp_path,
    )
    assert crits["AC-EVENT-01"]["ladder"] == set()


# ── 4. the inline `[ ]` block must not bleed into the NEXT criterion ──
def test_inline_flow_does_not_leak_to_next_criterion(tmp_path):
    crits = _parse(
        "- criterion_id: AC-COAL-01\n"
        "  verification_ladder: [lint, unit]\n"
        "- criterion_id: AC-COAL-02\n"
        "  name: \"no ladder\"\n",
        tmp_path,
    )
    assert crits["AC-COAL-01"]["ladder"] == {"lint", "unit"}
    assert crits["AC-COAL-02"]["ladder"] == set()
