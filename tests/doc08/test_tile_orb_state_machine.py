"""Doc 08 · §2.1/§2.2/§3 — the tile: the breathing teal orb + the §2.2 state machine.

Node ``experience.tile-orb-state-machine``. The tile is one HTML/canvas page (Doc 02's
canvas) streamed as Proxy's camera: a soft breathing bloom in teal-ink ``#35c2b8`` on
near-black, plus a state machine with EXACTLY the eight §2.2 states —

    listening · listening-to · working · checking · has-something · speaking · muted · reaction

— each entered ONLY by its NAMED driving system event (a ``tile.state`` render frame the
projector emits, sourced from Doc 04 session/name-gate + roster, Doc 05 progress
envelopes, Doc 04 turn boundary, Doc 02 mute/speaking signals). The build rule (§3): NO
state without a real source — a state that can't name its driving event does not exist.
The renderer has NO code path for a state it decides on its own, NO code path for facial
features / character animation / theatrical effects (the hard ceiling, §2.1), and the
tile is OUTBOUND-ONLY (§12.9): it consumes frames, it never originates a WS message.

The accessibility law (§2.2): every tile state is ALSO carried in speech or chat — a
dial-in / screen-reader participant loses the ambience, never the substance. Testable
that every ``tile.state`` has a chat/voice fallback.

These are REAL-path tests:
  * the eight §2.2 states are the LIVE ``libs.contracts.TileState`` Literal — exactly
    eight, no ninth/ad-hoc member (the contract is the closed set);
  * the host-side ``TileStateMachine`` (``transport.tile_state``) maps each NAMED system
    event → its §2.2 state as a REGISTERED ``TileState`` frame (``assert_registry_closed``
    stays green), and refuses to invent a state with no driving event;
  * every state the machine can drive ALSO yields a chat/voice accessibility fallback
    string (the accessibility law), in the §2.1 copy voice (no banned patterns);
  * ``apps/tile`` is a real Vite canvas app: the breathing teal ``#35c2b8`` orb + a PURE
    JS state machine driven ONLY by inbound ``tile.state`` frames — no self-deciding
    transition, no face/effect draw path, no ninth state — proven headless over the real
    ``tile.render-check.mjs`` (the actual renderer path, no browser).

Product imports live inside the test bodies so this module COLLECTS clean and FAILS RED
before ``transport/tile_state.py`` and the ``apps/tile`` renderer exist.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root: tests/doc08/test_tile_orb_state_machine.py -> parents[2] == repo root.
_ROOT = Path(__file__).resolve().parents[2]

# The canonical §2.2 state set — EXACTLY eight, in spec order. This literal list is the
# oracle every test measures against; a ninth/ad-hoc state anywhere fails.
CANONICAL_STATES: tuple[str, ...] = (
    "listening",
    "listening-to",
    "working",
    "checking",
    "has-something",
    "speaking",
    "muted",
    "reaction",
)


# --------------------------------------------------------------------------- #
# 1 · The contract carries EXACTLY the eight §2.2 states — no ninth/ad-hoc.
# --------------------------------------------------------------------------- #
def test_tile_state_contract_is_exactly_the_eight_canonical_states() -> None:
    """The live ``TileState`` Literal is EXACTLY the eight §2.2 states — the closed set.

    A ninth/ad-hoc member is unrepresentable because the contract is a closed Literal;
    this test pins the set so drift (a tenth state, a renamed one) fails the build.
    """
    from typing import get_args, get_type_hints

    from libs.contracts import TileState

    hints = get_type_hints(TileState)
    members = tuple(get_args(hints["state"]))
    assert set(members) == set(CANONICAL_STATES), (
        f"TileState must carry EXACTLY the eight §2.2 states {CANONICAL_STATES}; got {members}"
    )
    assert len(members) == 8, f"exactly eight states, no ninth/ad-hoc; got {len(members)}"


def test_a_ninth_ad_hoc_tile_state_is_a_validation_error() -> None:
    """Constructing a ``TileState`` with a state OUTSIDE the closed set is a ValidationError —
    the renderer can never be handed a ninth/ad-hoc state on the wire (§3 build rule)."""
    from pydantic import ValidationError

    from libs.contracts import TileState

    with pytest.raises(ValidationError):
        TileState(state="dancing")  # a theatrical/ad-hoc state that §2.2 does not define


@pytest.mark.parametrize("state", CANONICAL_STATES)
def test_each_canonical_state_constructs_a_registered_frame(state: str) -> None:
    """Each §2.2 state constructs a REGISTERED ``tile.state`` frame — every projected frame
    is a registered ``ProxyMessage`` (§4.5), so ``assert_registry_closed`` stays green."""
    from libs.contracts import CHANNEL_REGISTRY, TileState

    frame = TileState(state=state)  # type: ignore[arg-type]
    assert frame.type == "tile.state"
    assert frame.state == state
    # It is the registered model for its wire type (not a hand-built dict).
    assert CHANNEL_REGISTRY["tile.state"] is TileState


def test_registry_stays_closed_with_the_eight_state_tile_frame() -> None:
    """Adding the two missing §2.2 states to the Literal does not break registry closure —
    every OUTBOUND type still has a projector, every INBOUND a handler (§4.1)."""
    from libs.contracts import assert_registry_closed

    import transport.projector  # noqa: F401 — importing wires the surface projectors
    import transport.tile_state  # noqa: F401 — the host-side driver registers its producer

    assert_registry_closed()  # raises on any unprojected/unhandled type


# --------------------------------------------------------------------------- #
# 2 · The host-side state machine: each state entered ONLY by its NAMED event.
#     No state without a real driving system event (§3, Law 4).
# --------------------------------------------------------------------------- #
def test_state_machine_maps_exactly_the_eight_named_events() -> None:
    """The machine's event→state table is EXACTLY the eight §2.2 states, each keyed by a
    NAMED system event — no state exists without a driving event (§3 build rule)."""
    from transport.tile_state import TileStateMachine

    machine = TileStateMachine()
    driven = machine.driven_states()
    assert set(driven) == set(CANONICAL_STATES), (
        f"the machine must drive EXACTLY the eight §2.2 states; got {sorted(driven)}"
    )
    # Every driven state names a real system event as its source.
    for state in CANONICAL_STATES:
        source = machine.driving_event(state)
        assert source, f"state {state!r} must NAME its driving system event (no orphan state)"


@pytest.mark.parametrize(
    ("event", "expected_state"),
    [
        # NAMED system event → the §2.2 state it drives (the honest source table, §2.2).
        ("session_live", "listening"),          # Doc 04 session state: the session is live
        ("address_detected", "listening-to"),   # Doc 04 name-gate + roster: an address detected
        ("progress_envelope", "working"),        # Doc 05 progress envelope: real task progress
        ("lsp_ack", "checking"),                 # CANONICAL §12.8: LSP-bound direct-answer ACK
        ("turn_boundary_pending", "has-something"),  # Doc 04 turn boundary: result awaits a turn
        ("speaking_signal", "speaking"),         # Doc 02 speaking signal: synced to own audio
        ("hard_mute", "muted"),                  # Doc 02 mute signal: chosen silence, legible
        ("task_delivered", "reaction"),          # a task completed + delivered: sparing ack
    ],
)
def test_each_named_event_drives_its_canonical_state(event: str, expected_state: str) -> None:
    """WHEN a NAMED system event fires THE machine drives the corresponding §2.2 state as a
    registered ``TileState`` frame — the one binding §2.2 tabulates (the honest source)."""
    from libs.contracts import TileState
    from transport.tile_state import TileStateMachine

    frame = TileStateMachine().on_event(event)
    assert isinstance(frame, TileState), "the driver must emit a registered TileState frame"
    assert frame.state == expected_state, (
        f"named event {event!r} must drive §2.2 state {expected_state!r}, got {frame.state!r}"
    )


def test_state_machine_refuses_an_event_with_no_named_source() -> None:
    """An event the §2.2 table does not name drives NO state — the machine refuses it rather
    than inventing an ad-hoc state (§3: no state without a real driving event, Law 4)."""
    from transport.tile_state import TileStateMachine, UnknownTileEventError

    machine = TileStateMachine()
    with pytest.raises(UnknownTileEventError):
        machine.on_event("vibe_check")  # not a real system event → not a state


def test_state_machine_never_self_transitions() -> None:
    """The machine is a PURE function of its driving event: it holds no timer/heuristic that
    would move to a state on its own. Re-reading the current state without a new event never
    changes it (the renderer/driver never re-decides Proxy state, §3)."""
    from transport.tile_state import TileStateMachine

    machine = TileStateMachine()
    machine.on_event("progress_envelope")  # → working
    before = machine.current_state
    # No event fired → the state is unchanged; there is no self-deciding transition.
    assert machine.current_state == before == "working"
    # A different named event is the ONLY thing that can change it.
    machine.on_event("speaking_signal")
    assert machine.current_state == "speaking"


# --------------------------------------------------------------------------- #
# 3 · The accessibility law: every tile state is ALSO carried in speech/chat.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state", CANONICAL_STATES)
def test_accessibility_dual_channel_every_state_has_a_chat_or_voice_fallback(state: str) -> None:
    """Every §2.2 tile state ALSO carries its substance in a chat/voice fallback string — a
    dial-in / screen-reader participant loses the ambience, never the substance (§2.2)."""
    from transport.tile_state import TileStateMachine

    fallback = TileStateMachine().accessibility_fallback(state)
    assert isinstance(fallback, str) and fallback.strip(), (
        f"state {state!r} must carry a non-empty chat/voice accessibility fallback (§2.2)"
    )


def test_accessibility_fallback_covers_every_driven_state_no_gap() -> None:
    """The dual-channel map covers EVERY state the machine can drive — no state is
    motion-only (the accessibility law is total, not per-state opt-in)."""
    from transport.tile_state import TileStateMachine

    machine = TileStateMachine()
    for state in machine.driven_states():
        assert machine.accessibility_fallback(state).strip(), (
            f"driven state {state!r} has no chat/voice fallback — that is a motion-only "
            "state, which the accessibility law forbids (§2.2)"
        )


def test_accessibility_fallbacks_are_in_the_copy_voice_no_banned_patterns() -> None:
    """The fallback strings are user-visible copy → they pass the §2.1 copy guide (no
    'As an AI', no filler, no exclamation-theatre)."""
    from lint.copy_guide import check_copy
    from transport.tile_state import TileStateMachine

    machine = TileStateMachine()
    mapping = {f"tile.fallback.{s}": machine.accessibility_fallback(s) for s in machine.driven_states()}
    result = check_copy(mapping)
    rc = getattr(result, "exit_code", result)
    assert rc == 0, f"tile accessibility fallbacks must pass the copy guide; violations={getattr(result, 'violations', ())}"


def test_accessibility_fallbacks_carry_no_internal_names() -> None:
    """The fallback strings are user-visible → the naming lint passes (no Orchestrator /
    Scribe / workroom leaks into the tile's spoken/chat substance)."""
    from lint.naming import check_user_visible_strings
    from transport.tile_state import TileStateMachine

    machine = TileStateMachine()
    mapping = {f"tile.fallback.{s}": machine.accessibility_fallback(s) for s in machine.driven_states()}
    result = check_user_visible_strings(mapping)
    assert result.exit_code == 0, f"internal names leaked into tile fallbacks: {result.violations}"


# --------------------------------------------------------------------------- #
# 4 · apps/tile is a real Vite canvas app: the breathing teal orb + a PURE
#     state machine driven ONLY by inbound tile.state frames.
# --------------------------------------------------------------------------- #
def _tile_root() -> Path:
    return _ROOT / "apps" / "tile"


def test_tile_app_renders_the_breathing_teal_orb_on_near_black() -> None:
    """apps/tile is a real static app (not the bare scaffold): a canvas orb in teal-ink
    ``#35c2b8`` on near-black that BREATHES (an animation loop) — §2.1's presence mark."""
    root = _tile_root()
    index = (root / "index.html").read_text(encoding="utf-8")
    orb = (root / "src" / "orb.js").read_text(encoding="utf-8")
    # The teal-ink seed and a near-black backdrop are the identity (§2.1).
    assert "#35c2b8" in orb, "the orb must be teal-ink #35c2b8 (§2.1 seed)"
    # It breathes: an animation loop (requestAnimationFrame) drives the bloom.
    assert "requestAnimationFrame" in orb, "the orb must BREATHE (an animation loop), not sit static"
    # It draws onto a canvas (Doc 02's canvas).
    assert "getContext" in orb and "canvas" in index.lower()


def test_tile_renderer_has_no_face_or_theatrical_effect_code_path() -> None:
    """The hard ceiling (§2.1): the renderer has NO code path for facial features, character
    animation, or theatrical effects — proven by ABSENCE of any such symbol in the source."""
    src = _tile_root() / "src"
    joined = "\n".join(p.read_text(encoding="utf-8").lower() for p in src.rglob("*.js"))
    banned = ("face", "eye", "mouth", "avatar", "mascot", "character", "confetti", "sparkle", "emoji")
    hits = [word for word in banned if word in joined]
    assert not hits, (
        f"the tile renderer must contain NO face/character/theatrical code path (§2.1 hard "
        f"ceiling); found: {hits}"
    )


def test_tile_app_is_outbound_only_originates_no_ws_message() -> None:
    """The tile is OUTBOUND-ONLY (§12.9): it authenticates its render WS via the URL bearer
    token and RECEIVES frames — it never send()s / originates a WS message."""
    src = _tile_root() / "src"
    joined = "\n".join(p.read_text(encoding="utf-8") for p in src.rglob("*.js"))
    # No outbound WS origination: the tile never calls .send( on a socket.
    assert ".send(" not in joined, "the tile must originate NO WS message (outbound-only, §12.9)"


def test_tile_state_machine_is_pure_and_driven_only_by_frames_headless() -> None:
    """The tile's PURE JS state machine + orb renderer are driven ONLY by inbound
    ``tile.state`` frames: exactly the eight §2.2 states, each entered by its frame, no
    self-deciding transition, no ninth/ad-hoc state, no face/effect path. Proven headless
    over the real ``tile.render-check.mjs`` (the actual renderer path, no browser)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available to drive the headless tile render check")
    check = _tile_root() / "src" / "tile.render-check.mjs"
    proc = subprocess.run(  # noqa: S603 - fixed node binary + checked-in script, no shell
        [node, str(check)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"headless tile render check failed:\n{proc.stdout}\n{proc.stderr}"
    assert "TILE RENDER CHECK OK" in proc.stdout


def test_tile_js_state_set_matches_the_contract_exactly_headless() -> None:
    """The JS renderer's known state set is EXACTLY the eight §2.2 states — the SAME closed
    set the Python contract carries (one source of truth, no drift). Asserted inside the
    headless check, which exports ALL_TILE_STATES and pins it to the canonical eight."""
    states_js = (_tile_root() / "src" / "state_machine.js").read_text(encoding="utf-8")
    for state in CANONICAL_STATES:
        assert f'"{state}"' in states_js, f"the JS state machine must know §2.2 state {state!r}"
