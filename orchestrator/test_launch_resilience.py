"""Regression tests for launch resilience (TASK 3): the machine-sleep/suspend detector.

The 2026-07-21 run had a ~7h wall-clock gap (19:42 -> 02:35) with NO marker in any log — the
machine slept (idle/lid-close sleep freezes the whole process tree and can kill the tmux server),
and a later "why did it die?" had to be guessed. These tests lock in the detector that turns that
guesswork into a visible line: on the NEXT conductor start, a large gap since the last heartbeat is
logged LOUDLY. Crucially it must NOT false-alarm on a merely-long (but awake) phase — the heartbeat
thread keeps beating while awake, so only a real suspend produces a gap.

Run: .venv/bin/python -m pytest orchestrator/test_launch_resilience.py -q
"""
import importlib.util
import pathlib

ORCH = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("orchestrate", ORCH / "orchestrate.py")
orch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orch)


def test_no_warning_for_a_normal_short_gap():
    """A few-second restart gap (or a fresh heartbeat during an awake long phase) is silent."""
    assert orch._suspend_gap_message(1_000_000.0, 1_000_030.0, threshold=900) is None


def test_no_warning_right_at_the_threshold_boundary():
    assert orch._suspend_gap_message(1_000_000.0, 1_000_000.0 + 899, threshold=900) is None


def test_large_gap_is_flagged_as_probable_suspend():
    """A ~7h gap like the real incident MUST produce a loud, specific warning naming the minutes."""
    msg = orch._suspend_gap_message(1_000_000.0, 1_000_000.0 + 7 * 3600, threshold=900)
    assert msg is not None
    assert "420m" in msg or "7h" in msg or "420" in msg   # ~420 minutes
    low = msg.lower()
    assert "slept" in low or "suspend" in low
    assert "caffeinate" in low or "launch.sh" in low       # points at the prevention


def test_missing_heartbeat_file_returns_none(tmp_path, monkeypatch):
    """First run ever (no heartbeat yet) must not warn."""
    monkeypatch.setattr(orch, "_HEARTBEAT", tmp_path / "heartbeat")
    assert orch._read_last_heartbeat() is None


def test_corrupt_heartbeat_file_returns_none(tmp_path, monkeypatch):
    hb = tmp_path / "heartbeat"
    hb.write_text("not-a-number")
    monkeypatch.setattr(orch, "_HEARTBEAT", hb)
    assert orch._read_last_heartbeat() is None


def test_heartbeat_round_trips(tmp_path, monkeypatch):
    hb = tmp_path / "heartbeat"
    hb.write_text("1234567890")
    monkeypatch.setattr(orch, "_HEARTBEAT", hb)
    assert orch._read_last_heartbeat() == 1234567890.0


def test_launch_script_wires_the_full_hardening_contract():
    """orchestrator/launch.sh (TASK 5) must be the ONE command that wires every hardening property:
    whole-tree sleep guard, detached tmux (survives terminal closure), an external watchdog that
    outlives a tmux death, the double-launch guard, and a resume floor from tag+seal."""
    txt = (ORCH / "launch.sh").read_text()
    assert "caffeinate -dimsu" in txt, "whole-tree sleep guard (TASK 3) missing"
    assert "tmux new-session -d" in txt, "detached tmux (survive terminal closure) missing"
    assert "nohup" in txt and "watchdog.sh" in txt, "watchdog must run OUTSIDE tmux (survive its death)"
    assert "WATCH_TMUX=1" in txt, "watchdog dead-session check must be armed"
    assert "ALREADY LIVE" in txt, "double-launch guard (never mutate a live run) missing"
    assert "remain-on-exit" in txt, "halt banner must stay readable after the supervisor exits"
    assert "-done" in txt and "seal.json" in txt, "resume floor must consider BOTH tag and seal"


def test_main_checks_suspend_before_starting_a_fresh_heartbeat():
    """Ordering guard: main() must read the OLD heartbeat (suspend check) BEFORE overwriting it."""
    src = (ORCH / "orchestrate.py").read_text()
    body = src.split("def main(", 1)[1]
    assert "_note_suspend_gap()" in body and "_start_heartbeat()" in body
    assert body.index("_note_suspend_gap()") < body.index("_start_heartbeat()"), (
        "suspend gap must be checked before the new heartbeat overwrites the old one")
