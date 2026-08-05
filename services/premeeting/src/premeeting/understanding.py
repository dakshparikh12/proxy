"""The ONE resident understanding document = the qualitative COMPREHENSION + a compact NAV aid.

Pre-meeting's durable artifact is COMPREHENSION-FIRST — a holistic, qualitative mental model of the
codebase (:mod:`premeeting.comprehension`), the deep understanding a senior engineer who studied the
repo carries in their head. It is NOT a code index and NOT a line-number dump. Founder feedback:
what gets shovelled into the meeting agent's context is its resident mental model, so it must be
holistic comprehension + a mental GEOGRAPHY of where things live — never pasted code, never a symbol
index.

So the document is:

* the holistic COMPREHENSION on top — the STAR: what the system is, how it works end to end, the
  architecture, the domain, the conventions, the deep gotchas (verified against the real repo); then
* a COMPACT navigation aid underneath (:func:`premeeting.symbol_map.build_navigation_map`) — the
  top-level area map + the highest-rank DOMAIN entry points, so a reader knows WHERE to go. This is
  the demoted, trimmed Part 1: NOT the giant ranked-signatures body, NOT tests/scripts/archive noise.

**Law 1 reconciliation:** exact ``file:line`` grounding still happens — but LIVE in the meeting. The
agent uses this comprehension to know WHICH area to go to, then does a targeted lookup (grep) for the
precise citation at answer time. The resident understanding's job is the mental model + navigation.

:func:`build_understanding` composes them. When the comprehension pass could not run / did not
verify, the caller degrades to the deterministic symbol map ALONE (still complete + groundable) —
the comprehension only ever ADDS knowledge; it never blocks onboarding (Law 2). This combined text
is what :func:`premeeting.map_store` persists and what the warm meeting session loads RESIDENT +
cached (:func:`in_meeting.workroom.compose_resident_prime`).
"""
from __future__ import annotations

#: The header that OPENS the qualitative-comprehension section — the resident mental model. Explicit
#: so the reader (and the zero-read agent) knows this is comprehension to internalise, and the
#: navigation aid beneath it tells them WHERE to go (the exact file:line is looked up live).
_COMPREHENSION_HEADER = (
    "# Codebase understanding\n"
    "(Your resident mental model of this codebase — a holistic, qualitative comprehension, like a "
    "senior engineer who studied the repo. It is NOT a code index: it does not carry exact line "
    "numbers. Use it to understand the system and to know WHICH area to go to; then look up the "
    "exact `file:line` LIVE with a targeted search when you need to cite one. The compact navigation "
    "map beneath it is the geography — where things live at the area/module level.)\n\n"
)

#: The divider before the compact navigation aid.
_NAV_HEADER = "\n\n---\n\n"


def build_understanding(*, comprehension: str, navigation: str) -> str:
    """Compose the ONE resident understanding: the qualitative comprehension + a compact nav aid.

    ``comprehension`` is the holistic, qualitative mental model (empty ⇒ the caller degrades to the
    deterministic symbol map alone); ``navigation`` is the COMPACT area/entry-point map
    (:func:`premeeting.symbol_map.build_navigation_map`) — NOT the ranked-signatures dump. Returns
    the combined markdown: the comprehension on top under :data:`_COMPREHENSION_HEADER`, then the
    navigation aid. An empty ``navigation`` with a non-empty comprehension returns the comprehension
    alone (never a naked divider); an empty comprehension returns the navigation alone.
    """
    comp = (comprehension or "").strip()
    nav = (navigation or "").strip()
    if comp and nav:
        return _COMPREHENSION_HEADER + comp + _NAV_HEADER + nav + "\n"
    if comp:
        return _COMPREHENSION_HEADER + comp + "\n"
    return nav + "\n" if nav else ""


__all__ = ["build_understanding"]
