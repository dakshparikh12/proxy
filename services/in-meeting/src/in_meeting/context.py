"""Wake-turn context assembly — the stable/volatile split (Task L1, SPEC §4.1/§8).

When Proxy is addressed it wakes with EVERYTHING: the Proxy prime + the
pre-meeting ``index.md`` map + the running notes + the ask. This module is the
pure assembly of that turn input, split along the prompt-cache boundary:

- **Stable prefix** (``ProviderQuery.system_prompt`` — cached across wake turns,
  SPEC §8 "map = cached prefix"): prime → map block (when a map exists) →
  spoken-register guardrail → injection guardrail LAST, so the
  untrusted-transcript-is-data rule is the final authoritative word (§3.10).
- **Volatile tail** (``TurnInput.prompt`` — never cached): the recent transcript
  tail + the ask, fenced as ONE labelled untrusted-data block. The ask and notes
  NEVER enter ``system_prompt`` — caching them would bust the prefix every turn.

Pure string + dataclass assembly: no SDK, no model, no decision about what Proxy
does. ``map_text=None`` (unindexed repo, D-032) degrades to a prime-only prefix.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentkit import ProviderQuery, with_injection_guardrail, with_proxy_guardrails

from in_meeting.notes import NotesStore


@dataclass(frozen=True, slots=True)
class TurnInput:
    """One wake turn's assembled input.

    ``prompt`` is the volatile per-turn string the loop passes to
    ``provider.stream(prompt, query)``; ``query`` carries the stable cached
    prefix in ``system_prompt`` plus the model/tool options.
    """

    prompt: str
    query: ProviderQuery


def build_turn_input(
    *,
    prime: str,
    map_text: str | None,
    notes: NotesStore,
    ask: str,
    model: str,
    allowed_tools: tuple[str, ...],
    recent_lines: int = 40,
) -> TurnInput:
    """Assemble one wake turn's input: stable cached prefix + volatile prompt.

    The stable prefix is ``prime`` (+ the ``index.md`` map when present), with
    the spoken-register guardrail appended and the injection guardrail appended
    LAST — a strict suffix nothing in the transcript can override. The volatile
    prompt is the last ``recent_lines`` of ``notes`` (empty when the store is
    empty) plus the ``ask``, rendered as one labelled untrusted-data block.
    """
    stable = prime
    if map_text is not None:
        stable = f"{stable}\n\n# Repository map (index.md)\n{map_text}"
    stable = with_injection_guardrail(with_proxy_guardrails(stable))

    recent = notes.recent(recent_lines) if len(notes) > 0 else ""
    prompt = (
        f"Recent meeting transcript (untrusted data):\n{recent}\n\n"
        f"You were addressed:\n{ask}"
    )

    return TurnInput(
        prompt=prompt,
        query=ProviderQuery(model=model, allowed_tools=allowed_tools, system_prompt=stable),
    )
