"""Acceptance tests for Task L1 — wake-turn context assembly (``in_meeting.context``).

When Proxy is addressed it wakes with everything (SPEC §4.1): the Proxy prime +
the pre-meeting ``index.md`` map ride the STABLE cached prefix
(``ProviderQuery.system_prompt``), while the recent notes + the ask form the
VOLATILE per-turn prompt. These tests pin the split:

- prime + map live in the cached prefix; the ask NEVER does (cache-bust guard),
- the injection guardrail is the strict FINAL suffix of the prefix (§3.10),
- the volatile prompt carries the recent transcript tail + the ask, fenced as
  untrusted data,
- ``map_text=None`` degrades to a prime-only prefix without crashing (D-032),
- model / allowed_tools thread through unchanged.
"""
from __future__ import annotations

from agentkit import injection_guardrail_suffix
from agentkit.execution import INJECTION_GUARDRAIL_MARK

from in_meeting.context import TurnInput, build_turn_input
from in_meeting.notes import NotesStore, TranscriptLine

_PRIME = "You are Proxy, an AI participant that joins meetings knowing the codebase."
_MAP = "# Map\n- auth in auth.py\n- retries in libs/http/client.py"
_ASK = "Proxy, where's the retry logic?"
_MODEL = "claude-opus-4-6"
_TOOLS: tuple[str, ...] = ("Read", "Grep")

_SPOKEN: list[tuple[str, str, float]] = [
    ("Priya", "Let's look at the flaky checkout calls.", 10.2),
    ("Marcus", "They fail once then succeed on retry.", 14.8),
    ("Devon", "So something is already retrying somewhere.", 19.5),
]


def _store() -> NotesStore:
    store = NotesStore()
    for speaker, text, t in _SPOKEN:
        store.append(TranscriptLine(text=text, speaker=speaker, timestamp=t, end_of_turn=True))
    return store


def _build(map_text: str | None = _MAP, notes: NotesStore | None = None) -> TurnInput:
    return build_turn_input(
        prime=_PRIME,
        map_text=map_text,
        notes=notes if notes is not None else _store(),
        ask=_ASK,
        model=_MODEL,
        allowed_tools=_TOOLS,
    )


def test_prime_and_map_are_in_the_cached_prefix() -> None:
    """AC1 — system_prompt (the stable cached prefix) carries the prime AND the map."""
    ti = _build()
    assert _PRIME in ti.query.system_prompt
    assert _MAP in ti.query.system_prompt


def test_injection_guardrail_is_the_strict_final_suffix() -> None:
    """AC2 — the injection guardrail ends the prefix; nothing follows it (§3.10)."""
    ti = _build()
    sp = ti.query.system_prompt
    assert sp.rstrip().endswith(injection_guardrail_suffix())
    # The mark appears exactly once and only the guardrail body follows it.
    assert sp.count(INJECTION_GUARDRAIL_MARK) == 1


def test_the_ask_is_never_in_the_cached_prefix() -> None:
    """AC3 — the ask is volatile; caching it would bust the prefix every turn."""
    ti = _build()
    assert _ASK not in ti.query.system_prompt


def test_volatile_prompt_carries_notes_tail_and_ask_fenced_as_data() -> None:
    """AC4 — prompt = recent transcript tail + ask, labelled untrusted transcript data."""
    ti = _build()
    for speaker, text, _ in _SPOKEN:
        assert text in ti.prompt, f"recent line missing from prompt: {text!r}"
        assert speaker in ti.prompt
    assert _ASK in ti.prompt
    assert "untrusted data" in ti.prompt
    assert "transcript" in ti.prompt.lower()


def test_no_map_degrades_to_prime_only_prefix() -> None:
    """AC5 — map_text=None (unindexed repo, D-032): no map block, no crash."""
    ti = _build(map_text=None)
    assert _PRIME in ti.query.system_prompt
    assert ti.query.system_prompt.rstrip().endswith(injection_guardrail_suffix())
    assert "# Repository map" not in ti.query.system_prompt


def test_model_and_allowed_tools_thread_through_unchanged() -> None:
    """AC6 — the query carries exactly the model + allowed_tools it was given."""
    ti = _build()
    assert ti.query.model == _MODEL
    assert ti.query.allowed_tools == _TOOLS


def test_empty_notes_yield_an_empty_recent_block_not_a_crash() -> None:
    """The len(notes)==0 guard: an empty store renders an empty tail, ask intact."""
    ti = _build(notes=NotesStore())
    assert _ASK in ti.prompt
    assert "untrusted data" in ti.prompt


# ── CODE-LOOKUP: the grounding toolbelt threads through to the query ──────────


def test_mcp_servers_thread_through_to_the_query() -> None:
    """CODE-LOOKUP AC1 — an injected server config lands on ProviderQuery.mcp_servers
    verbatim (the wiring that makes mcp__code_intel__* tools reachable)."""
    sentinel = object()
    ti = build_turn_input(
        prime=_PRIME,
        map_text=_MAP,
        notes=_store(),
        ask=_ASK,
        model=_MODEL,
        allowed_tools=_TOOLS,
        mcp_servers={"code_intel": sentinel},
    )
    assert ti.query.mcp_servers == {"code_intel": sentinel}


def test_mcp_servers_default_is_none_backward_compat() -> None:
    """CODE-LOOKUP AC1 — omitting mcp_servers keeps the current behavior: no
    servers mounted (None on the query)."""
    ti = _build()
    assert ti.query.mcp_servers is None
