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
