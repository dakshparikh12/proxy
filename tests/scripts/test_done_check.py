"""Tests for done-check.sh.

TDD: these tests were written BEFORE the implementation.

Critical anti-gaming assertion: ./done-check.sh --spec 00 must return nonzero
when no tasks have passes:true and no _baseline.json exists.

Note: tests that invoke done-check.sh --spec are marked `integration` to
exclude them from the offline tier (they internally run a full pytest suite
which would cause nested-pytest conflicts with testmon if run concurrently
with the offline suite). Run directly:
    uv run pytest tests/scripts/test_done_check.py -p no:testmon
"""
import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DONE_CHECK = ROOT / "done-check.sh"


def _run_done_check(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    run_env = {**os.environ}
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", str(DONE_CHECK), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=run_env,
        timeout=300,  # 5 min max; the offline suite takes ~45s inside done-check
    )


def test_done_check_script_exists_and_is_executable() -> None:
    """done-check.sh must exist and be executable."""
    assert DONE_CHECK.exists(), f"{DONE_CHECK} does not exist"
    assert DONE_CHECK.stat().st_mode & 0o111, "done-check.sh must be executable"


@pytest.mark.integration
def test_spec_00_prints_conjunct_table() -> None:
    """--spec 00 must print the '== DONE(00) ==' header."""
    tasks_json = ROOT / "slices" / "00" / "tasks.json"
    if not tasks_json.exists():
        subprocess.run(
            ["uv", "run", "python3", "scripts/decompose.py", "00"],
            cwd=str(ROOT),
            check=True,
            timeout=30,
        )
    result = _run_done_check("--spec", "00")
    assert "DONE(00)" in result.stdout, (
        f"'DONE(00)' not found in stdout.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


@pytest.mark.integration
def test_spec_00_returns_nonzero_when_unbuilt() -> None:
    """CRITICAL anti-gaming assertion: --spec 00 must return nonzero (tasks unbuilt, no baseline).

    A done-check that returns 0 on unbuilt work is a total failure of the build system.
    This is marked integration to avoid nested-pytest conflicts in the offline suite.
    Run directly:
        uv run pytest tests/scripts/test_done_check.py::test_spec_00_returns_nonzero_when_unbuilt -p no:testmon
    """
    tasks_json = ROOT / "slices" / "00" / "tasks.json"
    if not tasks_json.exists():
        subprocess.run(
            ["uv", "run", "python3", "scripts/decompose.py", "00"],
            cwd=str(ROOT),
            check=True,
            timeout=30,
        )
    result = _run_done_check("--spec", "00")
    assert result.returncode != 0, (
        f"done-check.sh --spec 00 must return nonzero when tasks are unbuilt.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


@pytest.mark.integration
def test_spec_shows_all_tasks_pass_conjunct() -> None:
    """--spec must include the all-tasks-pass conjunct in output."""
    result = _run_done_check("--spec", "00")
    output = result.stdout + result.stderr
    assert "all-tasks-pass" in output, (
        f"'all-tasks-pass' not found in output.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


@pytest.mark.integration
def test_spec_shows_eval_baseline_info() -> None:
    """--spec must report eval status (blocked/pass) in output."""
    result = _run_done_check("--spec", "00")
    output = result.stdout + result.stderr
    has_eval_info = any(
        kw in output for kw in ["eval:", "BASELINE", "baseline", "eval>=baseline"]
    )
    assert has_eval_info, (
        f"Output must reference eval/baseline status.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_task_mode_null_cmd_returns_nonzero() -> None:
    """--task with a null acceptance.cmd must exit nonzero with BLOCKED message."""
    tasks_json = ROOT / "slices" / "00" / "tasks.json"
    if not tasks_json.exists():
        pytest.skip("slices/00/tasks.json not available")

    data = json.loads(tasks_json.read_text())
    null_task = next(
        (t for t in data["tasks"] if t.get("acceptance", {}).get("cmd") is None),
        None,
    )
    if null_task is None:
        pytest.skip("No null-cmd tasks in slices/00/tasks.json")

    task_id = null_task["task_id"]
    result = _run_done_check("--task", "00", task_id)
    assert result.returncode != 0, (
        f"--task with null cmd must return nonzero, got 0\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "BLOCKED" in (result.stdout + result.stderr), (
        f"Expected 'BLOCKED' in output for null-cmd task\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
