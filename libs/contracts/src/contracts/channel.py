"""The concrete ``ProxyMessage`` models (08-EXPERIENCE §4.2 / §4.4 / §4.5).

This is the canonical home for every wire message *body*: the one **inbound**
``ChannelAction`` (the generic surface-carrying family, §4.4) and every **outbound**
render frame (§4.5). ``registry.py`` owns only the machinery — the ``MessageType``
enum, the ``ProxyMessage`` base, the closure, and the produce/consume maps; the
models themselves live here so a shared type is never re-specified in prose
(CANONICAL §11.5). Importing this module fires ``__pydantic_init_subclass__`` on
every model, self-registering it into ``CHANNEL_REGISTRY`` (§4.1); ``contracts``'s
``__init__`` imports it so registration happens at package import.

Three field disciplines on the inbound ``ChannelAction`` are what the dispatch funnel
(§4.3) relies on — validated once, centrally, before a handler or a DB lookup runs:

* **``meeting_id: UUID``** — a non-UUID is rejected *before any DB lookup*, which is
  what makes tenant isolation SOUND: the funnel never queries attacker-shaped input.
  ``meeting_id`` is a UUID everywhere in the app tables (CANONICAL §11.2); only
  ``operation_runs.scope_id`` stays ``text`` (cast at the claim site — not here).
* **``Field(max_length=2000)`` on ``arg``** — an unbounded free-text field is a
  memory-exhaustion DoS vector; the field cap bounds it. (The socket-level payload
  cap is a *separate* bound owned by the gateway; this model owns the field cap.)
* **``Literal[...]`` closed sets on ``surface`` / ``action``** — an out-of-set value
  is a ``ValidationError``, not an ``if/else`` fall-through into an unintended path.

The **tile is OUTBOUND-ONLY** render (a human cannot click a video stream, CANONICAL
§12.9): it is never an inbound *origin*, so ``ActionSurface`` excludes ``"tile"`` and
there is **no ``TileAddress`` inbound model**. The connect page is REST (§4.6), not a
WS message — so there are no ``Connect*`` models here either. What remains inbound is
exactly the "share your screen / catch me up / keep answers shorter" family.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import Field

from .registry import CHANNEL_REGISTRY, ProxyMessage, register_field_consumer

# ── §2 render channels / §4.2 inbound-origin selectors ────────────────────────
# The five render channels (§2). The tile is one of them — but only as an OUTBOUND
# render target, never an inbound origin (see ``ActionSurface``).
Surface = Literal["voice", "chat", "tile", "canvas", "screen"]
# The inbound origins/targets a human can act FROM (§4.2). ``"tile"`` is EXCLUDED:
# a human cannot click a video stream (CANONICAL §12.9), so it never originates a
# ``channel_action``. Humans act by voice or ``@proxy`` chat, over canvas/screen.
ActionSurface = Literal["voice", "chat", "canvas", "screen"]


# ── inbound: the single generic surface-carrying family (§4.2 / §4.4) ─────────
class ChannelAction(ProxyMessage):
    """A human voice/chat command over the WS — the sole inbound client type (§4.2/§4.4).

    Validated once, centrally, at the dispatch funnel (§4.3): a non-UUID
    ``meeting_id``, an over-length ``arg``, or an out-of-set ``surface``/``action``
    is a ``ValidationError`` *before* any handler or DB lookup runs.
    """

    type: Literal["channel_action"] = "channel_action"
    # → sound isolation (§4.3): the funnel never runs a query on attacker-shaped input.
    meeting_id: UUID
    surface: ActionSurface  # the inbound origin (no "tile" — outbound-only, §12.9)
    action: Literal[
        "share_screen",
        "stop_share",
        "walkthrough_on",
        "walkthrough_off",
        "catch_me_up",
        "where_are_we",
        "shorter",
        "capabilities",
        "show_your_work",
    ]
    # Present only for surfaces that reference a rendered artifact (pin-to-source, the
    # final-artifact preview). The funnel resolves its OWNING meeting server-side (§4.3)
    # — a client ``canvas_id`` is never trusted to authorize the entity. (``Optional`` —
    # not PEP-604 ``|`` — so the id stays a UUID under annotation inspection.)
    canvas_id: Optional[UUID] = None  # noqa: UP045 — Optional keeps get_origin is Union for inspectors
    # The one free-text payload — CAPPED (§4.2 DoS bound); an unbounded ``arg`` is a
    # memory-exhaustion vector. The gateway's socket cap is a separate, second bound.
    arg: Annotated[str | None, Field(max_length=2_000)] = None


# ── outbound: the streamed render frames (§4.5) the projector renders to surfaces ─
# Every projected frame is a REGISTERED ``ProxyMessage`` instance — never a hand-built
# dict, never an unregistered ``"speak"`` type; ``send()`` serializes via ``model_dump()``.
class ResponseStart(ProxyMessage):
    """Open a streamed response on a surface (§4.5) — resolved FIRST, before any chunk."""

    type: Literal["response.start"] = "response.start"
    meeting_id: UUID


class ResponseChunk(ProxyMessage):
    """A ``send_chat()`` delivery-tool delta → the permanent chat record (§4.5)."""

    type: Literal["response.chunk"] = "response.chunk"
    response_id: Optional[UUID] = None  # noqa: UP045 — Optional keeps get_origin is Union
    chunk: Annotated[str, Field(max_length=8_000)]


class ResponseEnd(ProxyMessage):
    """Close a streamed response (§4.5)."""

    type: Literal["response.end"] = "response.end"
    meeting_id: UUID


class VoiceSpeak(ProxyMessage):
    """A ``speak()`` delivery-tool text delta → TTS (§4.5; never a bare ``speak`` dict)."""

    type: Literal["voice.speak"] = "voice.speak"
    text: Annotated[str, Field(max_length=8_000)]


class CanvasPatch(ProxyMessage):
    """A structured ``TOOL_RESULT`` / ``show_screen()`` render → canvas/screen (§4.5)."""

    type: Literal["canvas.patch"] = "canvas.patch"
    # the structured render payload (JSON string) — capped to bound the render DoS.
    patch: Annotated[str, Field(max_length=100_000)]


class ToolStart(ProxyMessage):
    """A work-tool ``TOOL_USE`` → the tile "working…" line (§4.5, §2.2)."""

    type: Literal["tool.start"] = "tool.start"
    line: Annotated[str, Field(max_length=200)]  # the humanized tool name


class TileState(ProxyMessage):
    """The tile lifecycle frame (§4.5): a closed Literal, driven server-side (Doc 04)."""

    type: Literal["tile.state"] = "tile.state"
    state: Literal[
        "listening",
        "working",
        "has-something",
        "speaking",
        "muted",
        "checking",
    ]


class NoteLine(ProxyMessage):
    """A decision/action/correction chat line (§2.4 #3,#4)."""

    type: Literal["note.line"] = "note.line"
    text: Annotated[str, Field(max_length=2_000)]


class DraftCard(ProxyMessage):
    """A staged-draft card (§2.4 #8) — links to the ``/m/`` accept route, never a raw URI."""

    type: Literal["draft.card"] = "draft.card"
    draft_id: UUID
    summary: Annotated[str, Field(max_length=2_000)]


# ── render-frame WIRE consumption (§4.5): every registered ProxyMessage is
# serialized WHOLE by ``send()`` (``model_dump()``) onto the surface wire, so its
# entire declared field set is consumed by construction. Registered structurally by
# walking each model's ``model_fields`` — NEVER a hand list — so a new frame field is
# consumed the moment it exists and can never orphan itself. (The §4.8 field-diff over
# ``MESSAGE_FIELD_CONSUMERS`` then only surfaces the DELIBERATE-drift cases: a standalone
# contract — AgentChunk/Envelope/ChannelReport — whose consumer reads a stale name.)
for _frame_model in CHANNEL_REGISTRY.values():
    register_field_consumer(_frame_model.__name__, *_frame_model.model_fields)


__all__ = [
    "ActionSurface",
    "CanvasPatch",
    "ChannelAction",
    "DraftCard",
    "NoteLine",
    "ResponseChunk",
    "ResponseEnd",
    "ResponseStart",
    "Surface",
    "TileState",
    "ToolStart",
    "VoiceSpeak",
]
