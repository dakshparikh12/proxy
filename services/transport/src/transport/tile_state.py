"""The tile state machine (Doc 08 §2.2/§3): named system event → §2.2 tile state.

The tile expresses EXACTLY the eight §2.2 states — listening · listening-to · working ·
checking · has-something · speaking · muted · reaction — and each is entered ONLY by its
NAMED driving system event. This module owns the honest event→state binding §2.2
tabulates: it is a **pure function of the driving event**, so a state can never be
entered without its real source and the tile is never handed a state it decided on its
own (§3 build rule; Law 4 — dynamic, never hard-coded self-deciding transitions).

The driving sources (§2.2, "the honest source"):

    session_live          → listening      the session is live (Doc 04 session state)
    address_detected      → listening-to   an address was detected (Doc 04 name-gate + roster)
    progress_envelope     → working        a real progress event (Doc 05's envelopes)
    lsp_ack               → checking       an LSP-bound direct-answer ACK ≤500ms (CANONICAL §12.8)
    turn_boundary_pending → has-something   a result awaits a turn boundary (Doc 04) — raise-a-hand
    speaking_signal       → speaking       synced to its own audio (Doc 02 speaking signal)
    hard_mute             → muted          the hard mute — silence legible as chosen (Doc 02)
    task_delivered        → reaction       a task completed + delivered — sparing (§2.2)

Each ``on_event`` returns a **registered** :class:`~libs.contracts.TileState` frame (never
a hand-built dict), so ``assert_registry_closed`` stays green and ``send()`` serializes it
via ``model_dump()`` (§4.5). An event the table does not name drives NO state — the
machine raises :class:`UnknownTileEventError` rather than invent an ad-hoc state.

**The accessibility law (§2.2).** Every tile state ALSO carries its substance in a
chat/voice fallback string — a dial-in / screen-reader participant loses the ambience,
never the substance. :meth:`accessibility_fallback` is total over the driven states (no
motion-only state), and the strings are in the §2.1 copy voice (no banned patterns, no
internal names — they pass the copy guide + naming lint).

This module holds NO renderer: what the orb *looks like* per state is ``apps/tile`` (the
Vite canvas app). It holds only the state binding + the dual-channel substance. It is
outbound-only by construction — it emits frames, it never reads an inbound tile message.
"""
from __future__ import annotations

from typing import Final

from libs.contracts import MessageType, TileState, register_producer


class UnknownTileEventError(ValueError):
    """An event the §2.2 source table does not name — it drives NO state (§3 build rule).

    Raised instead of inventing an ad-hoc/ninth state: a state that cannot name a real
    driving system event does not exist (Law 4 — no self-deciding transitions)."""


# The one honest event→state binding §2.2 tabulates. This dict IS the state machine: a
# named system event maps to exactly one §2.2 state, so no state is ever entered without
# its real source, and there is no ninth key (the eight values are the closed §2.2 set).
_EVENT_TO_STATE: Final[dict[str, str]] = {
    "session_live": "listening",
    "address_detected": "listening-to",
    "progress_envelope": "working",
    "lsp_ack": "checking",
    "turn_boundary_pending": "has-something",
    "speaking_signal": "speaking",
    "hard_mute": "muted",
    "task_delivered": "reaction",
}

# The accessibility law (§2.2): every state's substance ALSO in chat/voice. Plain,
# specific, warm — the §2.1 copy voice (no "As an AI", no filler, no exclamation-theatre,
# no internal names). A dial-in / screen-reader participant reads the substance here.
_STATE_TO_FALLBACK: Final[dict[str, str]] = {
    "listening": "I'm here and listening.",
    "listening-to": "Listening to you now.",
    "working": "On it — I'll post the result in chat.",
    "checking": "Checking — one moment.",
    "has-something": "I have the answer when there's a moment.",
    "speaking": "Speaking now — this is also going to chat.",
    "muted": "I'm muted — I'll keep taking notes.",
    "reaction": "Done — the result is in chat.",
}


class TileStateMachine:
    """The §2.2 tile state machine: a pure function of the driving system event.

    ``on_event(name)`` returns the registered :class:`TileState` frame for the §2.2 state
    that event drives, and advances ``current_state``. There is NO timer, heuristic, or
    self-deciding transition: the state only changes when a NAMED event fires (§3, Law 4).
    ``accessibility_fallback(state)`` returns that state's chat/voice substance (the
    accessibility law). The machine starts in ``listening`` (the session-live default).
    """

    def __init__(self) -> None:
        # Default per §2.2: "the session is live" → listening. No event has fired yet; the
        # tile breathes slowly. This is a value, not a decision — no self-transition.
        self._state: str = "listening"

    # --- oracles / introspection -------------------------------------------------------

    @property
    def current_state(self) -> str:
        """The state last driven by a named event — never changes without one."""
        return self._state

    def driven_states(self) -> tuple[str, ...]:
        """The EXACTLY-eight §2.2 states the machine can drive — the closed set (§3)."""
        return tuple(_EVENT_TO_STATE.values())

    def driving_event(self, state: str) -> str:
        """The NAMED system event that drives ``state`` (the honest source, §2.2).

        Every §2.2 state names its source; a state with no driving event does not exist,
        so this raises for an unknown state rather than returning an empty source.
        """
        for event, driven in _EVENT_TO_STATE.items():
            if driven == state:
                return event
        raise UnknownTileEventError(f"no §2.2 state named {state!r} — it has no driving event")

    # --- the pure event→state transition ----------------------------------------------

    def on_event(self, event: str) -> TileState:
        """Drive the §2.2 state its NAMED system event maps to, as a registered frame.

        An event the §2.2 table does not name drives NO state — raises
        :class:`UnknownTileEventError` rather than inventing an ad-hoc state (§3). The
        returned :class:`TileState` is a registered ``ProxyMessage`` instance the projector
        serializes with ``model_dump()`` (§4.5); the renderer receives only this closed set.
        """
        state = _EVENT_TO_STATE.get(event)
        if state is None:
            raise UnknownTileEventError(
                f"no §2.2 tile state is driven by event {event!r}: a state without a real "
                "driving system event does not exist (§3 build rule, Law 4)"
            )
        self._state = state
        return TileState(state=state)  # value is from the closed §2.2 set

    # --- the accessibility law (§2.2) --------------------------------------------------

    def accessibility_fallback(self, state: str) -> str:
        """The chat/voice substance for ``state`` — every state ALSO in speech/chat (§2.2).

        Total over the driven states (no motion-only state); raises for an unknown state so
        a gap surfaces as an error rather than a silent motion-only lapse.
        """
        fallback = _STATE_TO_FALLBACK.get(state)
        if fallback is None:
            raise UnknownTileEventError(
                f"no accessibility fallback for {state!r}: a motion-only tile state violates "
                "the accessibility law (§2.2)"
            )
        return fallback


# The host-side driver is a PRODUCER of ``tile.state`` frames (§4.8 field-diff / §4.1): it
# emits them from the named system events above. Register it so the produce/consume graph
# names a real producer for the type (the projector renders it; see transport.projector).
register_producer(MessageType.TILE_STATE, "transport.tile_state")
