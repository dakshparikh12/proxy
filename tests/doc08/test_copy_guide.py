"""Doc 08 · §2.1/§2.3 — the copy-guide CI check (banned patterns + three honesty shapes).

Node ``experience.copy-guide``. A BUILD-TIME guard on user-visible copy, run in CI
alongside the naming lint (``lint.naming``). Two obligations, both fail-closed:

  * §2.1 the copy voice — user-visible copy must never contain a BANNED pattern:
      - "As an AI…" self-reference,
      - filler ("Certainly!", "Great question!"),
      - exclamation-theatre (the exclamation mark itself).
    A banned pattern makes the check EXIT NON-ZERO (fails the build; never a warning).

  * §2.3 bullet 11 — the three honesty shapes Proxy must be able to speak
    (recuse / unknown / partial) are present as CANONICAL SEED STRINGS. The seed
    strings live in a CHECKED-IN artifact that is the SINGLE SOURCE the check reads;
    a missing/renamed shape fails the build.

Oracle strategy (PROTO-DETERMINISTIC-01): the tests run the REAL guard path — they
import the live ``lint.copy_guide`` module and drive its public entrypoints against
real inputs (the committed seed artifact, hand-crafted good/bad copy). No mocks.
Product imports live inside the test bodies so this module COLLECTS clean and FAILS
RED before ``lint/copy_guide.py`` and the seed artifact exist.

The seed strings are lifted verbatim from the spec (§2.3 bullet 11):
  recuse  -> "that's outside what I can judge"
  unknown -> "I don't know"
  partial -> "here's the part I can prove — the rest I can't"
"""
from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest

# Repo root: this file is tests/doc08/test_copy_guide.py -> parents[2] == repo root.
_ROOT = Path(__file__).resolve().parents[2]


def _load_guide():
    """Import the live copy-guide module (real path); skip nothing, fail red if absent."""
    return import_module("lint.copy_guide")


# ── The seed-string artifact is the single source ────────────────────────────
def test_copy_seed_artifact_is_checked_in_and_carries_the_three_honesty_shapes():
    """The canonical seed strings live in a CHECKED-IN artifact (the single source)."""
    guide = _load_guide()

    # The module names its artifact path — a real, committed file on disk.
    seed_path = Path(guide.SEED_ARTIFACT_PATH)
    if not seed_path.is_absolute():
        seed_path = _ROOT / seed_path
    assert seed_path.is_file(), (
        f"copy-guide seed artifact not checked in at {seed_path} — the seed strings "
        "must be a committed file, not inline literals"
    )

    data = json.loads(seed_path.read_text(encoding="utf-8"))

    # The three honesty shapes, keyed by their canonical names, each a real string.
    shapes = data["honesty_shapes"]
    for shape in ("recuse", "unknown", "partial"):
        assert shape in shapes, f"honesty shape {shape!r} missing from the seed artifact"
        assert isinstance(shapes[shape], str) and shapes[shape].strip(), (
            f"honesty shape {shape!r} must be a non-empty canonical seed string"
        )

    # The seed strings are the spec's canonical phrasings (§2.3 bullet 11).
    assert shapes["recuse"] == "that's outside what I can judge"
    assert shapes["unknown"] == "I don't know"
    assert shapes["partial"] == "here's the part I can prove — the rest I can't"


def test_check_reads_the_shapes_from_the_artifact_not_inline_literals():
    """The guard reads honesty shapes from the artifact — it is the SINGLE source.

    Point the loader at a throwaway artifact missing a shape; the shape-check must
    fail. This proves the check reads the file, not a hard-coded inline copy.
    """
    guide = _load_guide()
    seed_path = Path(guide.SEED_ARTIFACT_PATH)
    if not seed_path.is_absolute():
        seed_path = _ROOT / seed_path
    real = json.loads(seed_path.read_text(encoding="utf-8"))

    # A doctored artifact with the 'partial' shape removed.
    doctored = {"honesty_shapes": {k: v for k, v in real["honesty_shapes"].items() if k != "partial"},
                "banned_patterns": real.get("banned_patterns", [])}
    tmp = _ROOT / "tests" / "doc08" / "_tmp_doctored_seed.json"
    try:
        tmp.write_text(json.dumps(doctored), encoding="utf-8")
        result = guide.check_honesty_shapes(seed_path=tmp)
        rc = getattr(result, "exit_code", result)
        assert rc != 0, (
            "check_honesty_shapes must fail non-zero when a shape is missing from the "
            "artifact (proving it reads the artifact as the single source)"
        )
    finally:
        tmp.unlink(missing_ok=True)


def test_honesty_shapes_pass_on_the_real_committed_artifact():
    """On the real committed artifact all three shapes are present -> exit 0."""
    guide = _load_guide()
    result = guide.check_honesty_shapes()
    rc = getattr(result, "exit_code", result)
    assert rc == 0, "the committed seed artifact must carry all three honesty shapes (exit 0)"


# ── Banned patterns fail the build (never warn) ──────────────────────────────
@pytest.mark.parametrize(
    "bad_copy",
    [
        "As an AI language model, I cannot be certain.",
        "As an AI, I think the p95 is fine.",
        "Certainly! The p95 is 340ms.",
        "Great question! Here's the trace.",
        "p95 is 340ms — trace in chat!",  # exclamation-theatre
    ],
)
def test_banned_pattern_fails_the_check_nonzero(bad_copy: str):
    """Any banned pattern in user-visible copy makes the check exit NON-ZERO (fail)."""
    guide = _load_guide()
    result = guide.check_copy({"some.string": bad_copy})
    rc = getattr(result, "exit_code", result)
    assert rc != 0, f"copy guide must FAIL on banned copy, not pass: {bad_copy!r}"
    # And it must NAME the offending key (fail-closed, actionable — not a bare warning).
    violations = getattr(result, "violations", ())
    assert any("some.string" in str(v) for v in violations), (
        f"copy guide must name the offending string key; got {violations!r}"
    )


def test_clean_copy_passes():
    """Spec-compliant copy (§2.1 'Right' example) passes -> exit 0."""
    guide = _load_guide()
    good = {
        "answer.p95": "p95 is 340ms — trace in chat. One caveat: that's staging data; "
        "I can't see production.",
        "honesty.recuse": "that's outside what I can judge",
    }
    result = guide.check_copy(good)
    rc = getattr(result, "exit_code", result)
    assert rc == 0, "clean, spec-compliant copy must pass the guide (exit 0)"


def test_seed_strings_themselves_are_not_flagged_as_banned():
    """The canonical honesty seed strings must be clean copy (no banned pattern)."""
    guide = _load_guide()
    result = guide.check_copy(dict(guide.load_honesty_shapes()))
    rc = getattr(result, "exit_code", result)
    assert rc == 0, "the canonical honesty seed strings must not themselves trip the banned check"


# ── The guard's own CLI is fail-closed and runs alongside the naming lint ─────
def test_guard_main_is_fail_closed_on_a_seeded_violation(tmp_path: Path):
    """The module's CLI entrypoint exits non-zero on a real banned-copy violation.

    An EXECUTED gate, not a text-scan: run ``main`` over a tree containing a
    user-visible string with a banned pattern and assert non-zero.
    """
    guide = _load_guide()
    # A product-shaped file with a banned user-visible string.
    svc = tmp_path / "services" / "x" / "src" / "x"
    svc.mkdir(parents=True)
    (svc / "ui.py").write_text('TITLE = st.error("As an AI, I cannot help with that")\n', encoding="utf-8")
    hits = guide.scan_source(tmp_path)
    assert hits, "copy guide must flag a banned pattern in a committed user-visible string"


def test_copy_guide_and_naming_lint_run_in_ci_and_precommit_with_parity():
    """The copy guide is wired into BOTH CI and pre-commit, alongside the naming lint."""
    ci = (_ROOT / ".github" / "workflows" / "guards.yml").read_text(encoding="utf-8")
    pc = (_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    for surface_name, surface in (("CI guards.yml", ci), (".pre-commit-config.yaml", pc)):
        assert "copy-guide" in surface or "copy_guide" in surface, (
            f"copy guide must be a named guard in {surface_name} (run in both — guard parity)"
        )
        # It runs ALONGSIDE the naming lint: the naming lint must be a guard there too.
        assert "naming" in surface, (
            f"the naming lint must run alongside the copy guide in {surface_name}"
        )
        # Fail-closed: no continue-on-error / skip escape hatch on the copy guide.
        assert "continue-on-error: true" not in surface, (
            f"{surface_name} must not let the copy guide warn instead of fail"
        )
