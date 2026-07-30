"""Acceptance tests for Task L5 — the Proxy agent system prompt (``in_meeting.prompt``).

``PROXY_SYSTEM_PROMPT`` is the PRODUCT's behavior: the prime the live agent
session runs under in a meeting (SPEC §0/§4/§5; PROCESS §0 "two prompt homes").
These are the deterministic STRUCTURAL gates — the invariants, not the prose
(the wording is refined by the continuous-refinement loop; a judge battery
scores behavior later):

- identity is present and the meeting is framed as live,
- the FAITHFUL negative scan: the prompt is NOT a capability catalog — no
  scripted command tokens, no code, no numbered/bulleted capability menu,
  no "you can:"-style verb list (SPEC §1/§13 — the agent composes from access),
- the five load-bearing principles are present by tolerant case-insensitive
  keyword (concept present, sentence free),
- TTS-friendly: no markdown headers, no code fences, reasonably short,
- the prompt is exported (plus the trivial ``build_prime`` seam) so the
  deferred ConversationSimulator/G-Eval judge can load it.
"""
from __future__ import annotations

import re

import pytest

from in_meeting.prompt import PROXY_SYSTEM_PROMPT, build_prime


def test_nonempty_and_names_the_identity_and_live_meeting() -> None:
    """AC1 — a non-empty prime that says who Proxy is, in a LIVE meeting."""
    assert isinstance(PROXY_SYSTEM_PROMPT, str)
    assert len(PROXY_SYSTEM_PROMPT.strip()) > 200
    assert "Proxy" in PROXY_SYSTEM_PROMPT
    lower = PROXY_SYSTEM_PROMPT.lower()
    assert "meeting" in lower
    assert "live" in lower


def test_no_scripted_command_tokens() -> None:
    """AC2a — Faithful scan: no command/code tokens (the prompt is prose, not an API).

    Each check encodes one way a capability catalog leaks into a prompt:
    - ``name()`` call tokens (``mute()``, ``share_screen()``) — a scripted command,
    - ``def `` — literal code in the persona prompt,
    - snake_case identifiers (``share_screen``, ``catch_me_up``) — API names for
      capabilities instead of plain speech about access.
    """
    assert re.search(r"\w+\(\)", PROXY_SYSTEM_PROMPT) is None, "function-call token found"
    assert "def " not in PROXY_SYSTEM_PROMPT, "code definition found"
    assert re.search(r"\b[a-z]+_[a-z]+\b", PROXY_SYSTEM_PROMPT) is None, (
        "snake_case capability identifier found"
    )


def test_no_enumerated_capability_menu() -> None:
    """AC2b — Faithful scan: no numbered/bulleted menu, no "you can:" verb list.

    - a line starting ``1.`` / ``2)`` is a numbered menu (a voice reads it
      "one, two, three" — and it reads as a catalog),
    - a line starting ``- `` / ``* `` / ``• `` is a bulleted enumeration,
    - ``"you can:"`` introduces exactly the forbidden comma-list of verbs.
    """
    assert re.search(r"^\s*\d+[.)]\s", PROXY_SYSTEM_PROMPT, re.MULTILINE) is None, (
        "numbered menu line found"
    )
    assert re.search(r"^\s*[-*•]\s", PROXY_SYSTEM_PROMPT, re.MULTILINE) is None, (
        "bulleted enumeration line found"
    )
    assert re.search(r"you can\s*:", PROXY_SYSTEM_PROMPT, re.IGNORECASE) is None, (
        '"you can:" capability-list opener found'
    )


@pytest.mark.parametrize(
    ("principle", "alternatives"),
    [
        ("grounded claims (cite / file:line)", ("cite", "file:line")),
        ("human-click gate on world-touching actions", ("approve", "click")),
        ("ask when ambiguous", ("clarif", "ambigu")),
        ("honesty about limits", ("honest", "can't")),
        ("act by composing access", ("access", "compos")),
        # Law-2 under ABSENT access: no sandbox / no tool for it → say you can't
        # run it here; never narrate a run or a result that never happened.
        ("absent access is spoken, never faked", ("can't run", "cannot run", "didn't run")),
        ("never narrate an unperformed run", ("never narrate", "never describe a run")),
        # Ack-first even on quick lookups: the first words land BEFORE the first
        # tool call, so the room never waits in silence during a lookup.
        ("ack before the first tool call", ("before you reach", "before your first",
                                            "before any tool", "before the first tool")),
        # Law-1/2 on CONNECTIONS: how two facts interact (what reads what, which
        # limit bites) is asserted only after tracing that path — otherwise the
        # facts are stated and the link is named as unverified.
        ("untraced interactions are never asserted", ("interact", "facts connect",
                                                      "two facts")),
    ],
)
def test_load_bearing_principle_present(principle: str, alternatives: tuple[str, ...]) -> None:
    """AC3 — each load-bearing principle is present by tolerant keyword (any-of,
    case-insensitive): the CONCEPT is asserted, the sentence stays free."""
    lower = PROXY_SYSTEM_PROMPT.lower()
    assert any(alt in lower for alt in alternatives), f"principle missing: {principle}"


def test_tts_friendly() -> None:
    """AC4 — TTS-friendly prose: no markdown headers, no code fences, reasonably short."""
    assert re.search(r"^\s*#", PROXY_SYSTEM_PROMPT, re.MULTILINE) is None, "markdown header found"
    assert "```" not in PROXY_SYSTEM_PROMPT, "code fence found"
    assert len(PROXY_SYSTEM_PROMPT) < 6000, "prime too long to stay a cheap cached prefix"


def test_exported_for_the_judge_and_build_prime_seam() -> None:
    """AC5 — the prompt is importable for the deferred judge battery, and the
    trivial ``build_prime`` seam returns it verbatim (+ an optional access note)."""
    assert build_prime() == PROXY_SYSTEM_PROMPT
    composed = build_prime(access_note="The repo for this meeting is acme/checkout.")
    assert composed.startswith(PROXY_SYSTEM_PROMPT)
    assert composed.rstrip().endswith("The repo for this meeting is acme/checkout.")
