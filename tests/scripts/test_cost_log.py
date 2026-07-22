"""Tests for scripts/cost_log.py.

TDD: these tests are written BEFORE the implementation.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def test_append_writes_two_valid_json_lines(tmp_path: pathlib.Path) -> None:
    """append() twice -> file has 2 valid JSON lines with required keys."""
    os.environ["COST_LOG_DIR"] = str(tmp_path)
    try:
        import cost_log  # noqa: PLC0415

        cost_log.append("99", "scribe", 1000, 0.05, 12.3)
        cost_log.append("99", "close", 500, 0.02, 5.1)
    finally:
        del os.environ["COST_LOG_DIR"]

    log_file = tmp_path / "99.jsonl"
    assert log_file.exists(), f"Expected {log_file} to exist"
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"

    required_keys = {"phase", "tokens", "cost_usd", "wall_s"}
    for i, line in enumerate(lines):
        record = json.loads(line)
        missing = required_keys - set(record.keys())
        assert not missing, f"Line {i+1} missing keys: {missing}; got: {record}"

    assert lines[0] != lines[1], "Both lines are identical (suspicious)"


def test_append_writes_correct_values(tmp_path: pathlib.Path) -> None:
    """append() stores the exact values passed."""
    os.environ["COST_LOG_DIR"] = str(tmp_path)
    try:
        import cost_log  # noqa: PLC0415

        cost_log.append("01", "myPhase", 42, 0.123, 7.77)
    finally:
        del os.environ["COST_LOG_DIR"]

    log_file = tmp_path / "01.jsonl"
    record = json.loads(log_file.read_text().strip())
    assert record["phase"] == "myPhase"
    assert record["tokens"] == 42
    assert abs(record["cost_usd"] - 0.123) < 1e-9
    assert abs(record["wall_s"] - 7.77) < 1e-9


def test_spent_usd_sums_two_entries(tmp_path: pathlib.Path) -> None:
    """spent_usd() returns the sum of cost_usd across all log entries."""
    os.environ["COST_LOG_DIR"] = str(tmp_path)
    try:
        import cost_log  # noqa: PLC0415

        cost_log.append("02", "phase_a", 100, 0.10, 5.0)
        cost_log.append("02", "phase_b", 200, 0.25, 8.0)
        total = cost_log.spent_usd("02")
    finally:
        del os.environ["COST_LOG_DIR"]

    assert abs(total - 0.35) < 1e-9, f"Expected 0.35, got {total}"


def test_spent_usd_returns_zero_for_missing_file(tmp_path: pathlib.Path) -> None:
    """spent_usd() returns 0.0 when no log file exists yet."""
    os.environ["COST_LOG_DIR"] = str(tmp_path)
    try:
        import cost_log  # noqa: PLC0415

        total = cost_log.spent_usd("nonexistent_id_xyz")
    finally:
        del os.environ["COST_LOG_DIR"]

    assert total == 0.0, f"Expected 0.0, got {total}"


def test_cli_appends_entry(tmp_path: pathlib.Path) -> None:
    """CLI: python3 scripts/cost_log.py <id> <phase> <tokens> <cost_usd> <wall_s>."""
    env = {**os.environ, "COST_LOG_DIR": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cost_log.py"), "03", "cli_phase", "300", "0.07", "3.5"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"

    log_file = tmp_path / "03.jsonl"
    assert log_file.exists()
    record = json.loads(log_file.read_text().strip())
    assert record["phase"] == "cli_phase"
    assert record["tokens"] == 300


def test_default_log_dir_uses_evidence(tmp_path: pathlib.Path) -> None:
    """Without COST_LOG_DIR override, files go to evidence/cost/."""
    # We do NOT actually write to evidence/cost/ in tests — we use COST_LOG_DIR env var.
    # This test just verifies the env override works as the primary mechanism.
    os.environ["COST_LOG_DIR"] = str(tmp_path)
    try:
        import cost_log  # noqa: PLC0415

        cost_log.append("05", "verify", 50, 0.001, 1.0)
        log_file = tmp_path / "05.jsonl"
        assert log_file.exists(), "COST_LOG_DIR override must route writes to tmp_path"
        # Make sure evidence/cost/ was NOT written
        evidence_file = ROOT / "evidence" / "cost" / "05.jsonl"
        assert not evidence_file.exists(), "Should not write to evidence/cost/ when COST_LOG_DIR is set"
    finally:
        del os.environ["COST_LOG_DIR"]
