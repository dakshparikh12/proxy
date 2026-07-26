"""Doc 08 · §4.2/§4.4/§4.5 — the concrete Pydantic models in ``libs/contracts/channel.py``.

This node builds the NEW ``libs/contracts/channel.py`` module: the one inbound
``ChannelAction`` (the §4.2 generic-surface family) plus every OUTBOUND render frame
(§4.5), each a registered ``ProxyMessage`` subclass that self-registers at import.

The three §4.2 disciplines are what the dispatch funnel (§4.3) relies on and what
this test proves on the REAL model:

* **``meeting_id: UUID``** — a non-UUID is rejected *before any DB lookup*, which is
  what makes tenant isolation SOUND (the funnel never queries attacker-shaped input).
* **``Field(max_length=2000)`` on ``arg``** — an unbounded free-text field is a
  memory-exhaustion DoS vector; the cap bounds it.
* **``Literal[...]`` closed sets on ``surface`` / ``action``** — an out-of-set value
  is a ``ValidationError``, not an ``if/else`` fall-through into an unintended path.

``surface`` is ``ActionSurface`` — the inbound origins ``voice/chat/canvas/screen``
with **no ``tile``**: the tile is OUTBOUND-ONLY render (CANONICAL §12.9), never an
inbound origin, so there is no ``TileAddress`` inbound model.

Every test imports the LIVE ``libs.contracts`` package (triggering import-time
self-registration) and asserts against the real objects. No mocks. Product imports
live inside the test bodies so the module COLLECTS clean and fails RED before build.
"""
from __future__ import annotations

import types
from typing import Literal, Union, get_args, get_origin
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

# The exact §4.2 closed action set.
_ACTION_SET = {
    "share_screen",
    "stop_share",
    "walkthrough_on",
    "walkthrough_off",
    "catch_me_up",
    "where_are_we",
    "shorter",
    "capabilities",
    "show_your_work",
}
# §4.2 ActionSurface — inbound origins, tile EXCLUDED (outbound-only, CANONICAL §12.9).
_ACTION_SURFACES = {"voice", "chat", "canvas", "screen"}
# §4.5 outbound render frames.
_RENDER_FRAMES = {
    "VoiceSpeak",
    "ResponseStart",
    "ResponseChunk",
    "ResponseEnd",
    "CanvasPatch",
    "ToolStart",
    "TileState",
    "NoteLine",
    "DraftCard",
}


def _unwrap(ann: object) -> object:
    """Strip an ``X | None`` union down to ``X`` (both ``Union`` and PEP-604 ``|`` forms)."""
    if get_origin(ann) is Union or isinstance(ann, types.UnionType):
        arms = [a for a in get_args(ann) if a is not type(None)]
        return arms[0] if len(arms) == 1 else ann
    return ann


# ── the new module exists and exports the §4.2 + §4.5 models ──────────────────
def test_channel_module_exports_channel_action_and_render_frames() -> None:
    """libs/contracts/channel.py defines ChannelAction + every §4.5 render frame."""
    from libs.contracts import channel

    assert hasattr(channel, "ChannelAction"), "channel.py must define ChannelAction (§4.2)"
    for name in _RENDER_FRAMES:
        assert hasattr(channel, name), f"channel.py must define the §4.5 render frame {name!r}"


def test_channel_action_and_frames_are_registered_proxymessages() -> None:
    """Each model is a ProxyMessage subclass that self-registered into CHANNEL_REGISTRY."""
    from libs.contracts import CHANNEL_REGISTRY, ProxyMessage
    from libs.contracts import channel

    assert issubclass(channel.ChannelAction, ProxyMessage)
    assert CHANNEL_REGISTRY["channel_action"] is channel.ChannelAction
    frame_types = {
        "voice.speak": channel.VoiceSpeak,
        "response.start": channel.ResponseStart,
        "response.chunk": channel.ResponseChunk,
        "response.end": channel.ResponseEnd,
        "canvas.patch": channel.CanvasPatch,
        "tool.start": channel.ToolStart,
        "tile.state": channel.TileState,
        "note.line": channel.NoteLine,
        "draft.card": channel.DraftCard,
    }
    for key, model in frame_types.items():
        assert issubclass(model, ProxyMessage), f"{model.__name__} must be a ProxyMessage subclass"
        assert CHANNEL_REGISTRY[key] is model, f"{key!r} must be registered to {model.__name__}"


# ── §4.2 discipline 1 — meeting_id is a UUID, non-UUID raises ValidationError ─
def test_meeting_id_uuid_accepts_uuid_rejects_non_uuid() -> None:
    """meeting_id: UUID — a valid UUID parses, a non-UUID raises ValidationError."""
    from libs.contracts import channel

    ok = channel.ChannelAction(meeting_id=uuid4(), surface="voice", action="catch_me_up")
    assert isinstance(ok.meeting_id, UUID)

    with pytest.raises(ValidationError):
        channel.ChannelAction(meeting_id="not-a-uuid", surface="voice", action="catch_me_up")


def test_meeting_id_field_annotation_is_uuid() -> None:
    """The static annotation on meeting_id is UUID (not str) — the funnel relies on it."""
    from libs.contracts import channel

    ann = channel.ChannelAction.model_fields["meeting_id"].annotation
    assert _unwrap(ann) is UUID, f"meeting_id must be typed UUID, got {ann!r}"


# ── §4.2 discipline 2 — arg is capped at 2000; over-length raises ─────────────
def test_arg_capped_at_2000_over_length_raises() -> None:
    """arg is Annotated[str|None, Field(max_length=2000)]: 2000 ok, 2001 raises."""
    from libs.contracts import channel

    mid = uuid4()
    ok = channel.ChannelAction(meeting_id=mid, surface="chat", action="shorter", arg="x" * 2000)
    assert ok.arg is not None and len(ok.arg) == 2000

    with pytest.raises(ValidationError):
        channel.ChannelAction(meeting_id=mid, surface="chat", action="shorter", arg="x" * 2001)


def test_arg_max_length_is_declared_2000() -> None:
    """The Field(max_length=...) metadata on arg is exactly 2000 (the DoS bound)."""
    from libs.contracts import channel

    finfo = channel.ChannelAction.model_fields["arg"]
    caps = [getattr(m, "max_length", None) for m in (finfo.metadata or [])]
    caps = [c for c in caps if c is not None]
    assert 2000 in caps, f"arg must carry Field(max_length=2000), metadata caps={caps}"


# ── §4.2 discipline 3 — surface / action are closed Literals ──────────────────
def test_surface_is_actionsurface_literal_without_tile() -> None:
    """surface is a closed Literal == {voice, chat, canvas, screen}; 'tile' is NOT a member."""
    from libs.contracts import channel

    ann = channel.ChannelAction.model_fields["surface"].annotation
    assert get_origin(_unwrap(ann)) is Literal, "surface must be a Literal, not an open str"
    members = set(get_args(_unwrap(ann)))
    assert members == _ACTION_SURFACES, f"ActionSurface must be {_ACTION_SURFACES}, got {members}"
    assert "tile" not in members, "'tile' is OUTBOUND-ONLY (CANONICAL §12.9) — never an inbound origin"


def test_action_is_the_closed_4_2_literal() -> None:
    """action is the closed §4.2 Literal set — no extra, no missing member."""
    from libs.contracts import channel

    ann = channel.ChannelAction.model_fields["action"].annotation
    assert get_origin(_unwrap(ann)) is Literal, "action must be a Literal, not an open str"
    members = set(get_args(_unwrap(ann)))
    assert members == _ACTION_SET, f"action Literal must be {_ACTION_SET}, got {members}"


def test_out_of_set_surface_raises() -> None:
    """An out-of-set surface (e.g. the outbound-only 'tile') raises ValidationError."""
    from libs.contracts import channel

    with pytest.raises(ValidationError):
        channel.ChannelAction(meeting_id=uuid4(), surface="tile", action="catch_me_up")
    with pytest.raises(ValidationError):
        channel.ChannelAction(meeting_id=uuid4(), surface="nope", action="catch_me_up")


def test_out_of_set_action_raises() -> None:
    """An out-of-set action raises ValidationError (not a silent if/else fall-through)."""
    from libs.contracts import channel

    with pytest.raises(ValidationError):
        channel.ChannelAction(meeting_id=uuid4(), surface="voice", action="rm_rf_slash")


# ── tile is outbound-only: no TileAddress inbound model anywhere ───────────────
def test_no_inbound_tile_origin_model() -> None:
    """CANONICAL §12.9: there is NO TileAddress inbound model; tile is not an inbound origin."""
    from libs.contracts import channel

    assert not hasattr(channel, "TileAddress"), "tile is outbound-only — no TileAddress inbound model"
    # the sole INBOUND registry type stays channel_action (tile is never its origin).
    from libs.contracts import INBOUND

    assert {t.value for t in INBOUND} == {"channel_action"}


# ── canvas_id is an optional UUID (server resolves owning meeting; §4.3) ───────
def test_canvas_id_is_optional_uuid() -> None:
    """canvas_id: UUID | None — defaults None, a non-UUID raises."""
    from libs.contracts import channel

    a = channel.ChannelAction(meeting_id=uuid4(), surface="canvas", action="show_your_work")
    assert a.canvas_id is None
    cid = uuid4()
    b = channel.ChannelAction(
        meeting_id=uuid4(), surface="canvas", action="show_your_work", canvas_id=cid
    )
    assert b.canvas_id == cid
    with pytest.raises(ValidationError):
        channel.ChannelAction(
            meeting_id=uuid4(), surface="canvas", action="show_your_work", canvas_id="nope"
        )


# ── §4.5 render frames — ids are UUID, free text capped, selectors Literal ─────
def test_render_frames_field_discipline() -> None:
    """Every render frame: id fields UUID, free-text carries max_length, selectors Literal."""
    from typing import get_origin as _go

    from libs.contracts import channel

    frames = [
        channel.VoiceSpeak,
        channel.ResponseStart,
        channel.ResponseChunk,
        channel.ResponseEnd,
        channel.CanvasPatch,
        channel.ToolStart,
        channel.TileState,
        channel.NoteLine,
        channel.DraftCard,
    ]
    non_uuid: list[str] = []
    unbounded: list[str] = []
    open_selector: list[str] = []
    for model in frames:
        for fname, finfo in model.model_fields.items():
            if fname == "type":
                continue
            base = _unwrap(finfo.annotation)
            fl = fname.lower()
            if fl == "id" or fl.endswith("_id"):
                if not (base is UUID or getattr(base, "__name__", "") == "UUID"):
                    non_uuid.append(f"{model.__name__}.{fname}={base!r}")
                continue
            if fl in {"state", "status", "mode", "kind"}:
                if _go(base) is not Literal:
                    open_selector.append(f"{model.__name__}.{fname}={base!r}")
                continue
            if base is str:
                caps = [getattr(m, "max_length", None) for m in (finfo.metadata or [])]
                if not any(c is not None for c in caps) and getattr(finfo, "max_length", None) is None:
                    unbounded.append(f"{model.__name__}.{fname}")
    assert not non_uuid, f"render-frame id fields must be UUID: {non_uuid}"
    assert not unbounded, f"render-frame free-text must carry Field(max_length=...): {unbounded}"
    assert not open_selector, f"render-frame selectors must be Literal[...]: {open_selector}"


def test_tile_state_lifecycle_is_closed_literal() -> None:
    """TileState.state is the closed §2.2 lifecycle Literal — EXACTLY the eight §2.2 tile
    states, no open str and no ninth/ad-hoc member (node experience.tile-orb-state-machine
    owns the full set; §2.2 mandates listening-to and reaction alongside the other six)."""
    from libs.contracts import channel

    ann = channel.TileState.model_fields["state"].annotation
    assert get_origin(_unwrap(ann)) is Literal
    assert set(get_args(_unwrap(ann))) == {
        "listening",
        "listening-to",
        "working",
        "checking",
        "has-something",
        "speaking",
        "muted",
        "reaction",
    }


# ── the closure still holds with the new module driving registration ──────────
def test_registry_closed_with_channel_module() -> None:
    """assert_registry_closed() passes: channel.py's models close the graph, no drift."""
    from libs.contracts import assert_registry_closed

    assert assert_registry_closed() in (None, True)


# ── central funnel validator rejects a non-UUID / over-length before any handler ─
def test_validate_inbound_rejects_attacker_shaped_channel_action() -> None:
    """validate_inbound_message raises on a non-UUID meeting_id or an over-length arg (§4.3)."""
    from libs.contracts import validate_inbound_message

    good = {
        "type": "channel_action",
        "meeting_id": str(uuid4()),
        "surface": "voice",
        "action": "catch_me_up",
    }
    assert validate_inbound_message(good).action == "catch_me_up"

    with pytest.raises((ValueError, ValidationError)):
        validate_inbound_message({**good, "meeting_id": "not-a-uuid"})
    with pytest.raises((ValueError, ValidationError)):
        validate_inbound_message({**good, "action": "shorter", "arg": "x" * 2001})
    with pytest.raises((ValueError, ValidationError)):
        validate_inbound_message({**good, "surface": "tile"})
