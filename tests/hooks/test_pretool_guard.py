"""Tests for .claude/hooks/pretool_guard.py (subprocess-based)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "pretool_guard.py"


def _run_hook(payload: dict) -> tuple[int, str]:
    """Invoke the hook with the given payload on stdin; return (exit_code, stdout)."""
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip()


def _make_payload(tool_name: str, file_path: str) -> dict:
    return {"tool_name": tool_name, "tool_input": {"file_path": file_path}}


def test_edit_test_file_is_blocked() -> None:
    """Edit targeting tests/ must be blocked."""
    rc, out = _run_hook(_make_payload("Edit", "tests/doc03/test_x.py"))
    assert rc == 0
    data = json.loads(out)
    assert data["decision"] == "block"


def test_write_cassette_is_blocked() -> None:
    """Write to tests/cassettes/ (still under tests/) must be blocked."""
    rc, out = _run_hook(_make_payload("Write", "tests/cassettes/foo.yaml"))
    assert rc == 0
    data = json.loads(out)
    assert data["decision"] == "block"


def test_edit_baseline_json_is_blocked() -> None:
    """Edit targeting a _baseline.json file must be blocked."""
    rc, out = _run_hook(_make_payload("Edit", "slices/03/_baseline.json"))
    assert rc == 0
    data = json.loads(out)
    assert data["decision"] == "block"


def test_edit_product_code_is_allowed() -> None:
    """Edit targeting a normal service source file must be allowed (empty stdout, exit 0)."""
    rc, out = _run_hook(_make_payload("Edit", "services/scribe/src/scribe/close.py"))
    assert rc == 0
    assert out == ""


def test_non_write_tool_is_allowed() -> None:
    """A non-write tool (e.g. Bash) must always be allowed."""
    rc, out = _run_hook(_make_payload("Bash", "tests/doc03/test_x.py"))
    assert rc == 0
    assert out == ""


def test_absolute_path_test_file_is_blocked() -> None:
    """Absolute paths into tests/ must still be blocked."""
    rc, out = _run_hook(_make_payload("Write", "/repo/tests/doc03/test_abs.py"))
    assert rc == 0
    data = json.loads(out)
    assert data["decision"] == "block"


def test_malformed_payload_is_allowed() -> None:
    """A malformed JSON payload must fail-open (exit 0, no output)."""
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not-json",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
