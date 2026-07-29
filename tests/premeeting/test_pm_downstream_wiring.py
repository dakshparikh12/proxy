"""Downstream wiring — the map reaches the REAL wake + Workroom prompts (PM-DOWN-01/02/03).

These assert against the REAL emitted ``ProviderQuery.system_prompt`` (wake) and the REAL
rendered Workroom prompt — not a fake mount dict. The injection guardrail stays LAST.
"""
from __future__ import annotations

from typing import Any

import pytest

from agentkit import BehaviorConfig
from agentkit.execution import BehaviorRunner

_MAP = "# Repo Map — widget @ abc\n## Where things live\n- src/ — the app\n"


# ── PM-DOWN-01 (seam): the map rides the real ProviderQuery.system_prompt, guardrail last ──
def test_pm_down_01_context_prefix_reaches_system_prompt() -> None:
    cfg = BehaviorConfig(
        name="answer", role="You answer grounded codebase questions.",
        model="claude-sonnet", max_turns=4, tools=("speak",),
    )
    runner = BehaviorRunner(cfg, context_prefix=_MAP)
    query = runner.build_query(cfg, {})
    sp = query.system_prompt
    # The map is present in the REAL emitted system prompt.
    assert "# Repo Map — widget @ abc" in sp
    assert "src/ — the app" in sp
    # The injection guardrail is still the LAST authoritative segment (after the map + role).
    from agentkit import INJECTION_GUARDRAIL_MARK

    assert INJECTION_GUARDRAIL_MARK in sp
    assert sp.index("# Repo Map") < sp.index(INJECTION_GUARDRAIL_MARK)


def test_no_context_prefix_leaves_prompt_unchanged() -> None:
    cfg = BehaviorConfig(
        name="answer", role="Role text.", model="claude-sonnet", max_turns=1, tools=("speak",)
    )
    with_map = BehaviorRunner(cfg, context_prefix=_MAP).build_query(cfg, {}).system_prompt
    without = BehaviorRunner(cfg, context_prefix=None).build_query(cfg, {}).system_prompt
    assert "# Repo Map" in with_map and "# Repo Map" not in without
    assert "Role text." in without  # the role prompt is intact without a map


# ── PM-DOWN-01 (end-to-end): WakeTurn threads map_text → the runner's ProviderQuery ─────
def test_pm_down_01_wake_turn_mounts_map_prefix() -> None:
    from harness.wake_turn import WakeTurn

    class _P:
        name = "claude"

    wt = WakeTurn(meeting_id="m1", provider=_P(), map_text=_MAP)
    # The WakeTurn's runner carries the map as its context prefix (mounted onto every query).
    q = wt._runner.build_query(
        BehaviorConfig(name="b", role="r", model="claude-sonnet", max_turns=1, tools=("speak",)),
        {},
    )
    assert "# Repo Map — widget @ abc" in q.system_prompt


# ── PM-DOWN-02: the Workroom code-task prompt carries the same map as orientation ────────
def test_pm_down_02_workroom_bundle_prompt_carries_map() -> None:
    from workroom.session import SessionDriver

    class _Bundle:
        speaker = "alice"
        ask = "add a route"
        task_id = "t1"
        notes_ref = "m1"
        transcript_tail = "some chatter"

    driver = SessionDriver.__new__(SessionDriver)  # no db needed for the pure render
    prompt = SessionDriver._render_bundle_prompt(driver, _Bundle(), notes_text=None, map_text=_MAP)
    assert "Repo map (orientation" in prompt
    assert "# Repo Map — widget @ abc" in prompt
    # The map rides BEFORE the untrusted transcript tail (which stays fenced DATA).
    assert prompt.index("# Repo Map") < prompt.index("some chatter")


def test_pm_down_02_no_map_leaves_workroom_prompt_unchanged() -> None:
    from workroom.session import SessionDriver

    class _Bundle:
        speaker = "a"
        ask = "x"
        task_id = "t"
        notes_ref = "m"
        transcript_tail = "chatter"

    driver = SessionDriver.__new__(SessionDriver)
    prompt = SessionDriver._render_bundle_prompt(driver, _Bundle(), notes_text=None, map_text=None)
    assert "Repo map" not in prompt
    assert "Ask (from a): x" in prompt
