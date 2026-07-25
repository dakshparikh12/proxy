"""Session-scoped preferences (Doc 08 §2.4 #9) via the session-state digest.

Node: ``orchestrator.session-preferences`` (build-new). Spec refs: Doc 08 §2.4 #9
("Session preferences, spoken") and 04-ORCHESTRATOR §3.2 (the wake turn primed by
a compact session-state digest).

A participant steers Proxy in-meeting: *"keep answers shorter"*, *"stop posting
decision notes"*. Those preferences are **captured from chat**, **folded into the
session-state digest** every wake turn reads, and **held for the meeting only** —
never a hard-coded flag, never persisted past the session.

**Law 4 — dynamic, never hard-coded.** The mechanism is deliberately dumb: this
module PARSES a preference into a typed :class:`SessionPreference` and RENDERS it
into the digest text the wake turn reads. It never itself branches on a
preference's *value* to pick a behavior — the model's next turn honors the
"shorter answers" preference by reading the digest, and the deterministic
decision-note formatter honors "stop the notes" by reading ONE toggle
(:attr:`SessionPreferences.decision_notes_enabled`). The situation→action mapping
lives in model judgment + one formatter flag, not in an ``if pref: rewrite()``
branch here (the node's NOT-done clause forbids that).

**Session-scoped only (V0).** A :class:`SessionPreferences` is a plain in-memory
object created fresh per meeting; it touches no durable substrate (no Postgres, no
GCS), so a preference NEVER survives the meeting and a new meeting starts clean.
Persistent preferences are V1.

The recognition surface is a small **phrase→preference data table**
(:data:`_PHRASE_TABLE`) plus an optional injected ``classify`` judgment seam
(mirroring the name-gate's injected disambiguator) — so the *recognition* stays
data/judgment, not a hard-coded conditional cascade, and an ordinary ask
("where's the retry logic?") is recognized as NO preference and flows on to the
wake turn unchanged.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# ── the typed preference ──────────────────────────────────────────────────────

#: The product defaults a fresh meeting starts at: normal-length answers, decision
#: notes posted. A preference only ever moves a knob OFF this default for the
#: session; a new meeting re-reads the default (session-scoped-only).
_DEFAULT_VERBOSITY = "normal"
_DEFAULT_DECISION_NOTES_ENABLED = True


@dataclass(frozen=True)
class SessionPreference:
    """One captured, session-scoped preference as a typed ``(key, value)`` pair.

    ``key`` names the knob ("verbosity", "decision_notes"); ``value`` is the
    chosen setting ("short", "off"). It carries the verbatim ``source`` phrase so
    the harness can ACK precisely ("acknowledged and held", §2.4 #9). It is pure
    DATA — nothing here executes a behavior; the digest + model + formatter do.
    """

    key: str
    value: str
    source: str = ""


# ── recognition: a phrase→preference data table (+ injectable judgment seam) ───

# Each entry is (compiled surface-form regex, the SessionPreference it maps to).
# This is a DATA table of "how a human phrases this preference", NOT a behavior
# branch: it recognizes intent, it does not act on it. Ordering is irrelevant —
# every matching phrase contributes its preference (one chat line can carry two).
_PHRASE_SPECS: tuple[tuple[str, str, str], ...] = (
    # verbosity → short
    (r"\b(keep|make)\b.*\banswers?\b.*\b(short(er)?|brief(er)?|concise)\b", "verbosity", "short"),
    (r"\b(short(er)?|brief(er)?|concise)\b.*\banswers?\b", "verbosity", "short"),
    (r"\bbe\b.*\b(short(er)?|brief(er)?|concise|terse)\b", "verbosity", "short"),
    # decision_notes → off
    (r"\bstop\b.*\b(posting|the)?\b.*\bdecision\b.*\bnotes?\b", "decision_notes", "off"),
    (r"\bstop\b.*\bdecision\b.*\bnotes?\b", "decision_notes", "off"),
    (r"\bno\b.*\bdecision\b.*\bnotes?\b", "decision_notes", "off"),
    (r"\b(disable|silence|mute)\b.*\bdecision\b.*\bnotes?\b", "decision_notes", "off"),
)

_PHRASE_TABLE: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), key, value) for pat, key, value in _PHRASE_SPECS
)

#: An optional judgment seam: a callable taking the chat text and returning any
#: extra :class:`SessionPreference` list the mechanical table missed. Injected (not
#: constructed) so this module stays provider-free — the same discipline the
#: name-gate uses for its disambiguator. ``None`` → mechanical table only.
Classify = Callable[[str], list[SessionPreference]]


def _text_of(chat: Any) -> str:
    """Accept either a raw string or a transport ``ChatMessage`` (its ``.message``)."""
    msg = getattr(chat, "message", None)
    return msg if isinstance(msg, str) else str(chat)


def parse_preferences(chat: Any, *, classify: Classify | None = None) -> list[SessionPreference]:
    """Parse a chat line into the session-scoped preferences it expresses.

    Returns the recognized preferences (possibly two from one line), de-duplicated
    by ``(key, value)`` in first-seen order. An ordinary ask ("where's the retry
    logic?") expresses no preference → ``[]`` (it must flow on to the wake turn as
    an ASK, never be swallowed here). ``classify`` is an optional injected judgment
    seam whose extra preferences are appended to the mechanical table's.
    """
    text = _text_of(chat)
    found: list[SessionPreference] = []
    seen: set[tuple[str, str]] = set()

    def _add(key: str, value: str) -> None:
        if (key, value) not in seen:
            seen.add((key, value))
            found.append(SessionPreference(key=key, value=value, source=text))

    for pattern, key, value in _PHRASE_TABLE:
        if pattern.search(text):
            _add(key, value)

    if classify is not None:
        for pref in classify(text):
            _add(pref.key, pref.value)

    return found


# ── the session-scoped store ──────────────────────────────────────────────────


class SessionPreferences:
    """One meeting's live, in-memory preference set — session-scoped, never durable.

    Created fresh per meeting; holds the captured preferences until meeting end,
    then is discarded with the runtime. It exposes exactly two read surfaces the
    rest of the harness reads:

    * :meth:`render_for_digest` / :attr:`verbosity` — folded into the session-state
      digest so the model's next wake turn is primed by the preference (§3.2);
    * :attr:`decision_notes_enabled` — the ONE room-disable toggle the deterministic
      decision-note formatter honors (§2.4 #3/#9 "honors the session disable").

    It touches NO durable substrate: there is no Postgres/GCS write anywhere, so a
    preference cannot survive the meeting (session-scoped-only, V0).
    """

    def __init__(self) -> None:
        # Nothing durable: a plain dict from knob → chosen value. Empty == defaults.
        self._prefs: dict[str, str] = {}

    # -- capture ---------------------------------------------------------------

    def apply_chat(self, chat: Any, *, classify: Classify | None = None) -> list[SessionPreference]:
        """Capture any preference a chat line expresses; return what was captured.

        The caller ACKs precisely off the returned list ("acknowledged and held").
        A non-preference line captures nothing and moves no knob (returns ``[]``).
        """
        captured = parse_preferences(chat, classify=classify)
        for pref in captured:
            self._prefs[pref.key] = pref.value
        return captured

    def set(self, pref: SessionPreference) -> None:
        """Set one already-parsed preference on the store (idempotent per key)."""
        self._prefs[pref.key] = pref.value

    # -- read surfaces ---------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        """True when no preference has been captured (the meeting is at defaults)."""
        return not self._prefs

    @property
    def verbosity(self) -> str:
        """The requested answer length knob — the product default until steered."""
        return self._prefs.get("verbosity", _DEFAULT_VERBOSITY)

    @property
    def decision_notes_enabled(self) -> bool:
        """The single room-disable toggle the deterministic note formatter honors.

        ``True`` (posted) until a "stop posting decision notes" preference lands,
        then ``False`` for the rest of the meeting. The formatter reads THIS flag —
        it never re-parses chat — so suppression rides one boolean (Law 4).
        """
        if self._prefs.get("decision_notes") == "off":
            return False
        return _DEFAULT_DECISION_NOTES_ENABLED

    def as_dict(self) -> dict[str, str]:
        """A shallow copy of the captured knobs (for logging / the digest render)."""
        return dict(self._prefs)

    # -- the digest surface ----------------------------------------------------

    def render_for_digest(self) -> str:
        """Render the captured preferences as the digest's preference block.

        Empty store → ``""`` (free: the digest carries no preference block until a
        preference lands). Otherwise a compact, human-readable block naming each
        honored preference, so the model's next wake turn is primed by it. This is
        the WHOLE application path for "shorter answers": it becomes DATA in the
        session-state digest string, never a code branch (§3.2 / Law 4).
        """
        if not self._prefs:
            return ""
        lines: list[str] = ["session preferences (held for this meeting only):"]
        if self.verbosity != _DEFAULT_VERBOSITY:
            lines.append(f"  - keep answers {self.verbosity} — be concise")
        if not self.decision_notes_enabled:
            lines.append("  - do not post decision notes to chat (session-disabled)")
        return "\n".join(lines)


def fold_into_digest(base_digest: str, prefs: SessionPreferences) -> str:
    """Fold a meeting's captured preferences into the session-state digest text.

    The base digest (tasks in flight, mouth free/busy, component health — §3.2) is
    preserved verbatim; the preference block, if any, is appended after it. The
    wake turn reads the returned string, so a preference is "applied to subsequent
    wake turns" purely by being present in the digest — never by a branch.
    """
    block = prefs.render_for_digest()
    if not block:
        return base_digest
    return f"{base_digest}\n{block}"


__all__ = [
    "Classify",
    "SessionPreference",
    "SessionPreferences",
    "fold_into_digest",
    "parse_preferences",
]
