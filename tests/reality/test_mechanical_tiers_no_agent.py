"""Enforce (Task 6.2) the hard rule: the mechanical ladder tiers — lint / unit / integration —
NEVER spawn a claude agent. They are pure subprocess/script execution, zero token cost. Only the
cassette tiers (reality / negative / e2e) may reach a critic.

This is a STATIC enforcement so a future edit that accidentally routes a mechanical check through an
agent fails CI immediately, rather than silently burning tokens on a check that should be free.
"""
from __future__ import annotations

import inspect
import pathlib
import sys

ORCH = pathlib.Path(__file__).resolve().parents[2] / "orchestrator"
sys.path.insert(0, str(ORCH))

import ladder_runner  # noqa: E402

# Any token any code path would use to reach the agent layer.
AGENT_TOKENS = ("run_agent", "checked_agent", "critic(", "run_critic", "claude", "ladder_critics")


def test_mechanical_tier_functions_contain_no_agent_call() -> None:
    """The functions that execute lint/unit/integration must not reference the agent layer at all."""
    for fn in (ladder_runner._run_mechanical_tier, ladder_runner._mechanical_cmd):
        src = inspect.getsource(fn)
        hits = [t for t in AGENT_TOKENS if t in src]
        assert not hits, f"{fn.__name__} routes a MECHANICAL tier through an agent: {hits}"


def test_only_cassette_tiers_can_reach_the_critic() -> None:
    """The critic is invoked from exactly one place — the cassette-tier runner — and never from the
    mechanical path. Guards against a refactor that widens the critic call site."""
    run_ladder_src = inspect.getsource(ladder_runner.run_ladder)
    # In run_ladder, the critic is only handed to _run_cassette_tier (the else-branch); the mechanical
    # branch pulls from mech_result. Assert the mechanical branch does not call the critic.
    assert "critic" not in run_ladder_src.split("if t in MECHANICAL_TIERS:")[1].split("else:")[0], (
        "run_ladder's mechanical-tier branch references the critic — mechanical tiers must be agent-free"
    )
    # The tiers are partitioned: nothing is both mechanical and cassette-backed.
    assert not (set(ladder_runner.MECHANICAL_TIERS) & set(ladder_runner.CASSETTE_TIERS))


def test_cassette_tier_runner_is_the_sole_critic_caller() -> None:
    """_run_cassette_tier is the only function that calls the critic; the mechanical one does not."""
    assert "critic(" in inspect.getsource(ladder_runner._run_cassette_tier)
    assert "critic(" not in inspect.getsource(ladder_runner._run_mechanical_tier)
