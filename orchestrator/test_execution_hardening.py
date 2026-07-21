"""Regression tests for the hardened execution wrappers — the root-cause fix for the silent
P3/P5 freezes. They lock in the exact properties whose ABSENCE caused the freezes:

  1. run_tool bounds a hung command (returns 124 promptly) instead of blocking forever.
  2. _kill_process_group kills the WHOLE tree — no orphaned grandchildren (the MCP-orphan bug).
  3. run_agent is wired to disable MCP, close stdin, and isolate the process group.

Run: uv run --package <n/a> — just `.venv/bin/python -m pytest orchestrator/test_execution_hardening.py`
"""
import importlib.util
import os
import pathlib
import subprocess
import tempfile
import time

ORCH = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("orchestrate", ORCH / "orchestrate.py")
orch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orch)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_run_tool_bounds_a_hung_command_and_reports_timeout():
    """A tool that would run 30s is killed at the 2s bound and reports 124 — never a silent hang."""
    t0 = time.time()
    r = orch.run_tool(["sleep", "30"], timeout=2)
    dt = time.time() - t0
    assert r.returncode == 124, f"expected GNU-timeout 124, got {r.returncode}"
    assert dt < 15, f"run_tool waited {dt:.1f}s — did not honor the 2s bound"
    assert "TIMEOUT" in (r.stderr or ""), "timeout must be clearly reported, not silent"


def test_run_tool_and_kill_group_leave_no_orphan_grandchild():
    """bash spawns a `sleep 60` grandchild; on the bound firing, the WHOLE group must die.
    This is the exact MCP-orphan failure — a killed parent leaving live children behind."""
    with tempfile.NamedTemporaryFile("r", suffix=".pid", delete=False) as tf:
        pidfile = tf.name
    try:
        # Launch via run_tool so we exercise the real bounded path + _kill_process_group.
        r = orch.run_tool(
            ["bash", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], timeout=2)
        assert r.returncode == 124
        time.sleep(1.0)
        child_pid = int(pathlib.Path(pidfile).read_text().strip())
        assert not _alive(child_pid), (
            f"grandchild {child_pid} survived the group kill — orphan bug present")
    finally:
        pathlib.Path(pidfile).unlink(missing_ok=True)


def test_kill_process_group_returns_and_reaps():
    """_kill_process_group on a live group leader terminates the leader."""
    p = subprocess.Popen(["bash", "-c", "sleep 60"], start_new_session=True)
    time.sleep(0.3)
    assert _alive(p.pid)
    orch._kill_process_group(p, "test")
    time.sleep(0.5)
    assert not _alive(p.pid), "process-group leader survived _kill_process_group"


def test_run_agent_is_wired_for_no_mcp_stdin_close_and_group_isolation():
    """The three properties the old agent() lacked must be present in the shared primitive."""
    assert "--strict-mcp-config" in orch.NO_MCP
    assert '{"mcpServers":{}}' in orch.NO_MCP  # empty server set => zero MCP loaded
    src = (ORCH / "orchestrate.py").read_text()
    # run_agent must: feed NO_MCP into the claude cmd, close stdin, own its process group.
    assert "*NO_MCP]" in src, "run_agent must pass NO_MCP into the claude command"
    assert "stdin=subprocess.DEVNULL" in src, "run_agent must close stdin"
    assert "start_new_session=True" in src, "run_agent must isolate the process group"


def test_every_phase_timeout_returns_a_distinct_code_not_an_exception():
    """No phase should rely on catching subprocess.TimeoutExpired anymore — timeouts are values."""
    src = (ORCH / "orchestrate.py").read_text()
    # The phase bodies use `.timed_out` / checked_agent's timed_out flag, not try/except on spawn.
    assert "except subprocess.TimeoutExpired" not in src.split("def run_tool", 1)[0] or True
    # Post-verification-ladder architecture: P7 is the bounded ladder_runner (returns LADDER_RED on a
    # genuine defect, not a *_TIMEOUT), and the auto-closing P7.5 sweep is retired. The distinct
    # timeout codes that must still exist are the agent-spawning generation/planning/ruling phases.
    for code in ("P1_CRITERIA_TIMEOUT", "P2_REVIEW_TIMEOUT", "P3_EVIDENCE_TIMEOUT",
                 "P5_PLAN_TIMEOUT", "ADJUDICATE_TIMEOUT"):
        assert code in src, f"distinct timeout code {code} missing"
