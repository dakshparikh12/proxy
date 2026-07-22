"""Tests for scripts/journey.py — the integration-contract gate (Doc 09 §2)."""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
JOURNEY = str(ROOT / "scripts" / "journey.py")


def test_contracts_gate_runs() -> None:
    """Gate runs; returns 0 or 1; prints at least one 'registry' line."""
    r = subprocess.run(
        [sys.executable, JOURNEY, "contracts"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    combined = r.stdout + r.stderr
    assert r.returncode in (0, 1), f"unexpected returncode {r.returncode}:\n{combined}"
    assert "registry" in combined.lower(), f"expected 'registry' in output:\n{combined}"


def test_contracts_gate_outputs_per_check_lines() -> None:
    """Each of the 5 checks produces a PASS|FAIL|DEFERRED line."""
    r = subprocess.run(
        [sys.executable, JOURNEY, "contracts"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    output = r.stdout
    statuses = {"PASS", "FAIL", "DEFERRED"}
    lines_with_status = [line for line in output.splitlines() if any(s in line for s in statuses)]
    assert len(lines_with_status) >= 5, (
        f"expected at least 5 check lines (one per contract); got {len(lines_with_status)}:\n{output}"
    )


def test_registry_closed_passes() -> None:
    """The registry check should PASS on the assembled tree (contracts are closed)."""
    r = subprocess.run(
        [sys.executable, JOURNEY, "contracts"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    output = r.stdout
    registry_line = next((line for line in output.splitlines() if line.startswith("registry:")), None)
    assert registry_line is not None, f"no 'registry:' line in output:\n{output}"
    assert "PASS" in registry_line, (
        f"expected registry: PASS; got: {registry_line!r}\nfull output:\n{output}"
    )


def test_gate_never_crashes_on_valid_invocation() -> None:
    """The gate must not crash (returncode 2+ is internal error; 0 or 1 is expected)."""
    r = subprocess.run(
        [sys.executable, JOURNEY, "contracts"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    # returncode 0 = all pass, 1 = some fail/deferred, both are valid
    assert r.returncode in (0, 1), (
        f"gate crashed with returncode {r.returncode}:\nstdout={r.stdout}\nstderr={r.stderr}"
    )


def test_scenario_s1_resolves() -> None:
    """scenario S1 command locates the happy-arc e2e test and reports its id."""
    r = subprocess.run(
        [sys.executable, JOURNEY, "scenario", "S1"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    combined = r.stdout + r.stderr
    assert r.returncode == 0, f"scenario S1 failed:\n{combined}"
    assert "test_live_full_pipeline" in combined or "S1" in combined, (
        f"expected test id or S1 report in output:\n{combined}"
    )


def test_unknown_command_returns_2() -> None:
    """Unknown command should return exit code 2 (usage error)."""
    r = subprocess.run(
        [sys.executable, JOURNEY, "bogus-command"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert r.returncode == 2, f"expected returncode 2 for unknown command; got {r.returncode}"
