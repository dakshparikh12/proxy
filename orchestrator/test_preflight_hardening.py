"""Regression tests for the hardened cli_preflight — the root-cause fix for the 2026-07-21 doc03
silent restart-loop. The loop happened because a TRANSIENT Claude API outage made the
`claude -p "say hi"` probe exit non-zero ("Reached max turns (10)") or time out, and the old
preflight treated ANY non-zero returncode as a FATAL auth failure and sys.exit()'d to stderr ONLY
(never run.log). These tests lock in the exact properties whose ABSENCE caused the loop:

  1. A non-zero probe WITHOUT an auth signature (Reached max turns / ConnectionRefused / overloaded)
     is classified TRANSIENT, never 'auth' — so it is retried/backed-off, not treated as a login bug.
  2. A genuine auth signature (Invalid API key / please /login / 401) IS classified 'auth'.
  3. Every preflight failure is written to run.log (via log()), not just stderr — so the founder's
     canonical log shows the REAL reason, never a bare '[startup] host-tool PATH augmented'.
  4. Transient vs auth failures exit with DISTINCT codes so supervise.sh can back off vs hard-halt.

Run: .venv/bin/python -m pytest orchestrator/test_preflight_hardening.py -q
"""
import importlib.util
import pathlib

import pytest

ORCH = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("orchestrate", ORCH / "orchestrate.py")
orch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orch)


# ── 1. the classifier: transient MUST NOT be misread as auth (the exact root-cause bug) ──
def test_reached_max_turns_is_transient_not_auth():
    """The literal string that caused the loop. rc!=0 + 'Reached max turns' has NO auth signature."""
    assert orch._classify_preflight(1, "Error: Reached max turns (10)") == "transient"


def test_connection_refused_is_transient():
    assert orch._classify_preflight(1, "API Error: Unable to connect to API (ConnectionRefused)") == "transient"


def test_overloaded_and_5xx_are_transient():
    assert orch._classify_preflight(1, "Overloaded (529)") == "transient"
    assert orch._classify_preflight(1, "upstream error 503") == "transient"


def test_generic_nonzero_with_no_signature_is_transient_not_auth():
    """Fail SAFE: an unknown non-zero exit is transient (retryable), never a fatal auth stop."""
    assert orch._classify_preflight(1, "") == "transient"


def test_clean_probe_is_ok():
    assert orch._classify_preflight(0, "Hi! I'm ready to help.") == "ok"


# ── 2. genuine auth failures still hard-fail as 'auth' ──
@pytest.mark.parametrize("text", [
    "Invalid API key · Please run /login",
    "Error: Not authenticated",
    "401 Unauthorized",
    "403 Forbidden",
    "Your credentials have expired",
    "could not authenticate",
])
def test_real_auth_signatures_are_auth(text):
    assert orch._classify_preflight(1, text) == "auth"


# ── 3. distinct exit codes exist and differ ──
def test_distinct_preflight_exit_codes():
    assert orch.PREFLIGHT_EXIT_AUTH != orch.PREFLIGHT_EXIT_NETWORK
    assert orch.PREFLIGHT_EXIT_AUTH not in (0, 1)      # not confusable with a normal or generic exit
    assert orch.PREFLIGHT_EXIT_NETWORK not in (0, 1)


# ── 4. every preflight death writes the reason to run.log, not just stderr ──
def test_preflight_die_writes_reason_to_run_log(tmp_path, monkeypatch):
    """_preflight_die MUST append the reason to run.log (the founder's canonical log). The old
    sys.exit-to-stderr path is exactly why run.log looked silent."""
    fake_log = tmp_path / "run.log"
    monkeypatch.setattr(orch, "LOG", fake_log)
    with pytest.raises(SystemExit) as ei:
        orch._preflight_die(orch.PREFLIGHT_EXIT_NETWORK, "network/API degraded — this is the reason")
    assert ei.value.code == orch.PREFLIGHT_EXIT_NETWORK
    written = fake_log.read_text()
    assert "PRELAUNCH FAIL" in written
    assert "network/API degraded — this is the reason" in written


def test_source_no_longer_treats_bare_nonzero_as_auth():
    """Guard against regressing to `if r.returncode != 0 or has_auth_error: ...auth...`."""
    src = (ORCH / "orchestrate.py").read_text()
    assert "r.returncode != 0 or has_auth_error" not in src, (
        "the bare-returncode==auth misclassification (the root-cause bug) must not return")
