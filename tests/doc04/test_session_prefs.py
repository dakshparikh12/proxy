"""Doc 04 · session-scoped preferences via the session-state digest.

Node: ``orchestrator.session-preferences`` (build-new). Spec refs: Doc 08 §2.4 #9
("Session preferences, spoken") and 04-ORCHESTRATOR §3.2 (the wake turn primed by
a compact session-state digest).

Doc 08 §2.4 #9, verbatim: '"Proxy, keep answers shorter" / "stop posting decision
notes" → acknowledged and held for the session (it lands in Proxy's session-state
digest — Doc 04 — and shapes behavior). No settings page; the meeting *is* the
settings page. (Session-scoped only in V0; persistent preferences are V1.)'

Node definition-of-done: "a preference expressed in chat ('keep answers shorter',
'stop posting decision notes') is captured into the session-state digest, survives
a wake-turn recycle within the same meeting, is applied by subsequent turns
(shorter output / suppressed decision-note posting), and is scoped to the session
(a new meeting starts clean). NOT done: a preference that persists across meetings,
one applied via a hard-coded conditional rather than the digest, or one that is
captured but not actually honored by later turns."

Node acceptance: "WHEN a participant states a session preference in chat THE
SYSTEM SHALL fold it into the session-state digest and apply it to subsequent wake
turns for the remainder of the meeting only."

Invariants asserted here:
  * Law 4 (dynamic, never hard-coded) — the preference lives in the digest string
    the wake turn reads, NOT in an ``if pref == ... : ...`` branch inside the turn.
  * session-scoped only — a fresh meeting gets a fresh, empty preference set; a
    preference NEVER persists across meetings.

Scenario covered: ``E-session-preference-shorter-answers-stop-decision-notes`` —
a participant types "Proxy, keep answers shorter and stop posting decision notes";
both preferences are parsed, folded into the digest, applied to later answers
(shorter) and to the deterministic decision-note chat line (suppressed via the
room disable toggle) — never a hard-coded flag, never persisted past the session.

Product imports live INSIDE the test bodies (or are guarded) so this module
COLLECTS clean and FAILS red before
``services/harness/src/harness/behaviors/session_prefs.py`` exists.
"""
from __future__ import annotations

import pathlib

from transport.signals import ChatMessage

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = (
    _ROOT
    / "services"
    / "harness"
    / "src"
    / "harness"
    / "behaviors"
    / "session_prefs.py"
)


# ── parsing a chat line into a structured, session-scoped preference ──────────


def test_shorter_answers_is_parsed_from_chat() -> None:
    """"keep answers shorter" parses to a verbosity=short preference."""
    from harness.behaviors.session_prefs import parse_preferences

    prefs = parse_preferences("Proxy, keep answers shorter")

    assert any(p.key == "verbosity" and p.value == "short" for p in prefs)


def test_stop_posting_decision_notes_is_parsed_from_chat() -> None:
    """"stop posting decision notes" parses to decision_notes=off."""
    from harness.behaviors.session_prefs import parse_preferences

    prefs = parse_preferences("Proxy, stop posting decision notes")

    assert any(p.key == "decision_notes" and p.value == "off" for p in prefs)


def test_one_line_can_carry_two_preferences() -> None:
    """The scenario's line carries BOTH prefs; both are parsed from the one line."""
    from harness.behaviors.session_prefs import parse_preferences

    prefs = parse_preferences(
        "Proxy, keep answers shorter and stop posting decision notes"
    )
    keys = {(p.key, p.value) for p in prefs}

    assert ("verbosity", "short") in keys
    assert ("decision_notes", "off") in keys


def test_a_non_preference_line_parses_to_nothing() -> None:
    """An ordinary ask ("where's the retry logic?") is NOT a preference.

    A grounded question must flow to the wake turn as an ASK, never be swallowed as
    a preference — so the parser returns no preferences on a non-preference line.
    """
    from harness.behaviors.session_prefs import parse_preferences

    assert parse_preferences("Proxy, where's the checkout retry logic?") == []


def test_parse_is_case_insensitive() -> None:
    """Preference recognition does not depend on casing."""
    from harness.behaviors.session_prefs import parse_preferences

    prefs = parse_preferences("PROXY, KEEP ANSWERS SHORTER")
    assert any(p.key == "verbosity" and p.value == "short" for p in prefs)


# ── the session-scoped store: capture, apply, hold for the meeting ────────────


def test_store_starts_empty_and_default() -> None:
    """A fresh meeting's preferences are empty — full verbosity, notes ON.

    Session-scoped means "no memory at meeting start": the defaults are the
    product defaults (normal-length answers, decision notes posted).
    """
    from harness.behaviors.session_prefs import SessionPreferences

    prefs = SessionPreferences()
    assert prefs.is_empty is True
    assert prefs.verbosity == "normal"
    assert prefs.decision_notes_enabled is True


def test_apply_a_chat_preference_updates_the_store() -> None:
    """Applying the scenario's chat line flips BOTH knobs on the store."""
    from harness.behaviors.session_prefs import SessionPreferences

    prefs = SessionPreferences()
    prefs.apply_chat("Proxy, keep answers shorter and stop posting decision notes")

    assert prefs.verbosity == "short"
    assert prefs.decision_notes_enabled is False
    assert prefs.is_empty is False


def test_apply_chat_returns_only_the_recognized_preferences() -> None:
    """apply_chat reports what it captured so the harness can ACK precisely.

    (Doc 08 §2.4 #9: the preference is "acknowledged and held" — the caller needs
    to know WHICH prefs landed to speak the acknowledgement.)
    """
    from harness.behaviors.session_prefs import SessionPreferences

    prefs = SessionPreferences()
    captured = prefs.apply_chat("Proxy, keep answers shorter")

    assert [(p.key, p.value) for p in captured] == [("verbosity", "short")]


def test_a_non_preference_line_leaves_the_store_untouched() -> None:
    """Applying an ordinary ask captures nothing and changes no knob."""
    from harness.behaviors.session_prefs import SessionPreferences

    prefs = SessionPreferences()
    captured = prefs.apply_chat("Proxy, what's the retry policy?")

    assert captured == []
    assert prefs.is_empty is True
    assert prefs.verbosity == "normal"
    assert prefs.decision_notes_enabled is True


# ── the digest surface: preferences shape what EVERY wake turn reads ──────────


def test_default_store_contributes_nothing_to_the_digest() -> None:
    """With no preference set, the digest carries no preference block (free)."""
    from harness.behaviors.session_prefs import SessionPreferences

    assert SessionPreferences().render_for_digest() == ""


def test_captured_preferences_render_into_the_session_state_digest() -> None:
    """The applied preferences render as a text block the wake turn's digest reads.

    This is the whole mechanism (§3.2 / Law 4): the preference becomes DATA in the
    session-state digest string, not a code branch. The render must name both
    honored preferences so the model's next turn is primed by them.
    """
    from harness.behaviors.session_prefs import SessionPreferences

    prefs = SessionPreferences()
    prefs.apply_chat("Proxy, keep answers shorter and stop posting decision notes")
    block = prefs.render_for_digest().lower()

    assert "short" in block
    assert "decision note" in block


# ── Law 4: the mapping lives in the digest/model, never a hard-coded branch ───


def test_source_has_no_situation_conditional_on_the_preference_value() -> None:
    """Static floor (Law 4): no ``if verbosity == 'short'`` behavior branch here.

    The preference is FOLDED INTO THE DIGEST and honored by the model's judgment
    on the next turn (and, for decision-notes, by the deterministic formatter's
    single room-disable toggle). This module must NOT itself branch on the
    preference VALUE to pick a behavior — that would be the hard-coded mapping the
    node's NOT-done clause forbids. We assert the source contains no such branch.
    """
    src = _SRC.read_text(encoding="utf-8")
    lowered = src.lower()
    # No behavior-selection branch keyed on a preference value.
    assert 'if verbosity ==' not in lowered.replace(" ", "")
    assert 'ifself.verbosity==' not in lowered.replace(" ", "")
    assert "shorten(" not in lowered, "the module holds the preference; it does not itself re-write answers"


# ── session-scoped ONLY: never persisted, a new meeting starts clean ──────────


def test_two_meetings_do_not_share_preferences() -> None:
    """A preference set in one meeting NEVER leaks into another meeting.

    Session-scoped-only (V0): each meeting owns its own store; there is no shared
    or persisted preference state. Meeting B starts at the product defaults even
    after meeting A shortened its answers.
    """
    from harness.behaviors.session_prefs import SessionPreferences

    meeting_a = SessionPreferences()
    meeting_a.apply_chat("Proxy, keep answers shorter")

    meeting_b = SessionPreferences()

    assert meeting_a.verbosity == "short"
    assert meeting_b.verbosity == "normal", "a fresh meeting must start clean"
    assert meeting_b.decision_notes_enabled is True


def test_no_persistence_seam_in_the_source() -> None:
    """Static floor: the store touches no DB/GCS/durable write (session-scoped only).

    A preference that reached Postgres/GCS would survive the meeting — the node's
    top NOT-done clause. The in-memory store must import no durable-substrate seam.
    We inspect the parsed IMPORTS (not prose in docstrings) so a docstring that
    merely NAMES the excluded substrates cannot false-positive.
    """
    import ast

    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("asyncpg", "psycopg", "sqlalchemy", "google.cloud.storage", "substrate")
    for mod in imported:
        for durable in forbidden:
            assert durable not in mod, f"session preferences must not import a durable seam ({mod!r})"
    # And no direct `.commit(` call anywhere in the source (a transactional write).
    assert ".commit(" not in _SRC.read_text(encoding="utf-8")


# ── integration: the preference actually reaches the wake turn's digest ───────


def test_preference_folds_into_the_digest_a_wake_turn_reads() -> None:
    """End-to-end shape (node acceptance + scenario): a chat preference folded into
    the session-state digest is what the NEXT wake turn is primed with.

    We build the digest the wake turn reads AFTER a preference lands, and assert the
    honored preference is present in that digest text — proving the preference is
    "applied to subsequent wake turns", via the digest, not a branch.
    """
    from harness.behaviors.session_prefs import SessionPreferences, fold_into_digest

    prefs = SessionPreferences()
    prefs.apply_chat("Proxy, keep answers shorter and stop posting decision notes")

    base_digest = "tasks in flight: 0 · mouth: free · components: healthy"
    digest = fold_into_digest(base_digest, prefs)

    # The base state survives untouched...
    assert base_digest in digest
    # ...and the honored preferences are now part of what the turn reads.
    assert "short" in digest.lower()
    assert "decision note" in digest.lower()


def test_decision_note_disable_toggle_is_exposed_for_the_formatter() -> None:
    """The deterministic decision-note formatter honors ONE room-disable toggle.

    Doc 08 §2.4 #3/#9: the decision/action chat line is a deterministic formatter
    keyed on a committed note_delta — it "honors the session disable". The store
    exposes that single boolean toggle (``decision_notes_enabled``) so the formatter
    reads one flag rather than re-parsing chat — the room disable toggle from the
    scenario. Suppression is via that flag, never a second parse path.
    """
    from harness.behaviors.session_prefs import SessionPreferences

    prefs = SessionPreferences()
    assert prefs.decision_notes_enabled is True  # default: posted
    prefs.apply_chat("Proxy, stop posting decision notes")
    assert prefs.decision_notes_enabled is False  # disabled for the session


def test_chatmessage_object_is_accepted_by_apply() -> None:
    """apply_chat accepts the transport ChatMessage the run-loop already carries.

    The name-gate hands a ``ChatMessage`` forward; the preference path consumes the
    same shape (its ``.message`` text) so no re-shaping is needed at the call site.
    """
    from harness.behaviors.session_prefs import SessionPreferences

    prefs = SessionPreferences()
    msg = ChatMessage(message="Proxy, keep answers shorter", sender="Sam")
    captured = prefs.apply_chat(msg)

    assert [(p.key, p.value) for p in captured] == [("verbosity", "short")]
    assert prefs.verbosity == "short"
