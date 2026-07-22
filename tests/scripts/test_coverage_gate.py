import subprocess, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_coverage_gate_runs_on_doc00_bundle():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/coverage_gate.py"), "doc00"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode in (0, 1), r.stderr
    assert "requirement" in (r.stdout + r.stderr).lower()


def test_coverage_gate_parses_real_requirements():
    sys.path.insert(0, str(ROOT / "scripts"))
    import coverage_gate
    reqs = coverage_gate.parse_requirements(ROOT / "acceptance/doc00/requirements/requirements.yaml")
    assert len(reqs) > 0
    # bundle carries doc requirements (R-DOC00-*) plus cross-cutting invariants (R-INV-*)
    assert all(k.startswith("R-") for k in reqs)
    assert any(k.startswith("R-DOC00") for k in reqs)


def test_parse_criteria_includes_test_ids():
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import coverage_gate
    importlib.reload(coverage_gate)  # ensure fresh load after path insert
    crits = coverage_gate.parse_criteria(ROOT / "acceptance/doc00/criteria/criteria.yaml")
    assert len(crits) > 0
    # All criteria dicts must carry a test_ids key (may be empty list for some)
    assert all("test_ids" in c for c in crits), "parse_criteria must include 'test_ids' key in each dict"
    # At least some criteria must have non-empty test_ids
    assert any(len(c["test_ids"]) > 0 for c in crits), "at least some criteria must have non-empty test_ids"
    # Spot-check: AC-CMP-001 should have T-CMP-001
    cmp001 = next((c for c in crits if c["id"] == "AC-CMP-001"), None)
    assert cmp001 is not None
    assert "T-CMP-001" in cmp001["test_ids"]
