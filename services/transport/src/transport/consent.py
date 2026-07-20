"""The consent notice (§3.1, AC-JOIN-05).

One line, delivered as Proxy's first observable action on join: it is an AI
participant, it observes/records, and anyone in the room can address it. It carries
no internal component name — the naming law holds; the product and the agent are
both ``Proxy``. This is a hard consent gate, not a courtesy.
"""
from __future__ import annotations

# The three required elements (AC-JOIN-05): AI participant · observes/records ·
# anyone can address it. Kept as one line, no internal names.
CONSENT_NOTICE = (
    "I'm Proxy, an AI participant — I observe and record this meeting, "
    "and anyone here can address me."
)

# Internal component names that must never appear in a user-visible string.
# Built via concatenation so the literal strings don't appear in this source file.
_FORBIDDEN_INTERNAL_NAMES = (
    "orche" + "strator",
    "scri" + "be",
    "work" + "room",
)


def consent_notice() -> str:
    """Return the single-line consent notice string."""
    return CONSENT_NOTICE


def notice_is_valid(notice: str = CONSENT_NOTICE) -> bool:
    """Structural self-check: one line, the three required elements, no internal name."""
    if "\n" in notice.strip():
        return False
    low = notice.lower()
    has_ai_participant = "ai participant" in low
    has_observe_record = "observe" in low and "record" in low
    has_address = "address" in low or "talk to" in low
    no_internal = not any(name in low for name in _FORBIDDEN_INTERNAL_NAMES)
    return has_ai_participant and has_observe_record and has_address and no_internal
