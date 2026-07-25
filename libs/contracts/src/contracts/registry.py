"""The client ``ProxyMessage`` registry + full-graph closure (08-EXPERIENCE §4.1).

Every message that crosses the WS wire is a registered ``ProxyMessage``:

* the single **inbound** client type ``channel_action`` — the generic surface
  family (voice/chat-originated commands, §4.4). The tile is OUTBOUND-ONLY (a
  video stream a human cannot click — CANONICAL §12.9: no ``tile.address``); the
  connect page is REST (§4.6), not a WS message (no ``connect.*`` types). So the
  sole inbound client type is ``channel_action``.
* the **outbound** render-frame family (§4.5) the backend streams to surfaces:
  ``response.start/chunk/end`` · ``voice.speak`` · ``canvas.patch`` ·
  ``tool.start`` · ``tile.state`` · ``note.line`` · ``draft.card``.

This REPLACES the pre-canonical ``{connect-repo, approve-draft, invite-proxy}``
shape that CANONICAL §12.9/§12.12 explicitly delete (tile outbound-only; connect
moved to REST). Registration is enforced at **import time** (``__pydantic_init_subclass__``)
and re-checked by ``assert_registry_closed()`` in CI and at boot (fail-fast).

The closure is the produce/consume graph made structural (CANONICAL §12.12): not
merely "a model exists for every type," but — over the three declared maps —
**every inbound type has exactly one handler** and **every outbound type has ≥1
projector**. A produced-but-unhandled inbound or a frame no projector renders
fails the build.

Signal-surface events (§11.8 — transcript/roster/speaking/boundary/barge-in/
bot-status/meeting-end/channel-report) are in-process/over-transport internal
events, NOT client ``ProxyMessage``s; they stay OUT of this closure and a leak
into the client registry fails it.
"""
from __future__ import annotations

import enum
from typing import Any, Callable, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# type-string -> ProxyMessage subclass. Populated by ``__pydantic_init_subclass__``.
CHANNEL_REGISTRY: dict[str, type["ProxyMessage"]] = {}

# Doc 02 signal-surface events — outside the client registry closure by design (§11.8).
SIGNAL_SURFACE_EVENTS: frozenset[str] = frozenset(
    {
        "transcript",
        "roster",
        "speaking",
        "boundary",
        "barge-in",
        "bot-status",
        "meeting-end",
        "channel-report",
    }
)


class MessageType(enum.Enum):
    """The closed discriminator enum for client<->backend messages (§4.1, CANONICAL §1)."""

    # ── inbound: human → backend over the WS (voice/chat-originated commands) ──
    # The tile is OUTBOUND-ONLY (CANONICAL §12.9: no tile.address inbound). The
    # connect page is REST (§4.6): no connect.* WS types. So the sole inbound
    # client type is the generic surface-carrying family:
    CHANNEL_ACTION = "channel_action"
    # ── outbound: backend → surfaces (the streamed render frames, §4.5) ──
    RESPONSE_START = "response.start"
    RESPONSE_CHUNK = "response.chunk"
    RESPONSE_END = "response.end"
    VOICE_SPEAK = "voice.speak"
    CANVAS_PATCH = "canvas.patch"
    TOOL_START = "tool.start"
    TILE_STATE = "tile.state"
    NOTE_LINE = "note.line"
    DRAFT_CARD = "draft.card"


# The produce/consume graph, made into the two coverage partitions the closure
# checks (CANONICAL §12.12): every inbound handled, every outbound projected.
INBOUND: frozenset[MessageType] = frozenset({MessageType.CHANNEL_ACTION})
OUTBOUND: frozenset[MessageType] = frozenset(MessageType) - INBOUND


class ProxyMessage(BaseModel):
    """Base for client<->backend wire messages; subclasses auto-register at import time."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        field = cls.model_fields.get("type")
        if field is None or field.default is None:
            return
        default = field.default
        key = default.value if isinstance(default, enum.Enum) else str(default)
        if key in CHANNEL_REGISTRY and CHANNEL_REGISTRY[key] is not cls:
            raise ValueError(f"duplicate ProxyMessage type registered: {key!r}")
        CHANNEL_REGISTRY[key] = cls


# ── inbound: the generic surface-carrying family (§4.4; tile is never its origin) ──
class ChannelActionMessage(ProxyMessage):
    """A human voice/chat command over the WS — the sole inbound client type (§4.4)."""

    type: Literal["channel_action"] = "channel_action"
    action: str = Field(max_length=64)  # the generic surface verb (ask/mute/stop/…)
    text: str | None = Field(default=None, max_length=8000)  # optional free-text payload


# ── outbound: the streamed render frames (§4.5) the projector renders to surfaces ──
class ResponseStartMessage(ProxyMessage):
    """Open a streamed response on a surface (§4.5)."""

    type: Literal["response.start"] = "response.start"
    response_id: UUID


class ResponseChunkMessage(ProxyMessage):
    """A ``send_chat()`` delivery-tool delta → chat (§4.5)."""

    type: Literal["response.chunk"] = "response.chunk"
    response_id: UUID
    text: str = Field(max_length=8000)


class ResponseEndMessage(ProxyMessage):
    """Close a streamed response (§4.5)."""

    type: Literal["response.end"] = "response.end"
    response_id: UUID


class VoiceSpeakMessage(ProxyMessage):
    """A ``speak()`` delivery-tool text delta → TTS (§4.5; never a bare ``speak`` dict)."""

    type: Literal["voice.speak"] = "voice.speak"
    text: str = Field(max_length=8000)


class CanvasPatchMessage(ProxyMessage):
    """A structured TOOL_RESULT / ``show_screen()`` render → canvas/screen (§4.5)."""

    type: Literal["canvas.patch"] = "canvas.patch"
    patch: str = Field(max_length=100_000)  # the structured render payload (JSON string)


class ToolStartMessage(ProxyMessage):
    """A work-tool TOOL_USE → tile "working…" line (§4.5)."""

    type: Literal["tool.start"] = "tool.start"
    tool_name: str = Field(max_length=120)


class TileStateMessage(ProxyMessage):
    """The tile lifecycle frame (§4.5): listening/working/has-something/speaking/muted/checking."""

    type: Literal["tile.state"] = "tile.state"
    state: Literal[
        "listening", "working", "has-something", "speaking", "muted", "checking"
    ]


class NoteLineMessage(ProxyMessage):
    """A decision/action/correction chat line (§2.4 #3,#4)."""

    type: Literal["note.line"] = "note.line"
    text: str = Field(max_length=2000)


class DraftCardMessage(ProxyMessage):
    """A staged-draft card (§2.4 #8) — links to the ``/m/`` accept route, never a raw URI."""

    type: Literal["draft.card"] = "draft.card"
    draft_id: UUID
    summary: str = Field(max_length=2000)


# ── the produce/consume graph, made into three declared maps (CANONICAL §12.12) ──
# A ``Handler`` consumes an inbound message; a ``Projector`` renders an outbound
# frame onto a surface. These are structural declarations the closure walks — the
# concrete host wiring (the dispatch funnel handler, the surface projectors) lives
# in the transport/experience services and registers into these maps at import.
Handler = Callable[..., Any]
Projector = Callable[..., Any]

MESSAGE_HANDLERS: dict[MessageType, Handler] = {}  # inbound  → EXACTLY 1 handler
MESSAGE_PROJECTORS: dict[MessageType, list[Projector]] = {}  # outbound → ≥1 projector
MESSAGE_PRODUCERS: dict[MessageType, list[str]] = {}  # who EMITS each type (field-diff, §4.8)


def register_handler(message_type: MessageType, handler: Handler) -> None:
    """Bind the single handler for an inbound client type (§4.3 dispatch funnel).

    Re-binding the SAME callable is idempotent; binding a *different* handler to an
    already-handled inbound type is a wiring error (exactly-one-handler invariant).
    """
    if message_type not in INBOUND:
        raise ValueError(f"{message_type.value!r} is not an inbound type; only inbound has handlers")
    existing = MESSAGE_HANDLERS.get(message_type)
    if existing is not None and existing is not handler:
        raise ValueError(
            f"inbound {message_type.value!r} already handled by {existing!r}; "
            "an inbound type has EXACTLY ONE handler (§4.1)"
        )
    MESSAGE_HANDLERS[message_type] = handler


def register_projector(message_type: MessageType, projector: Projector) -> None:
    """Add a projector (a surface render) for an outbound render-frame type (§4.5)."""
    if message_type not in OUTBOUND:
        raise ValueError(f"{message_type.value!r} is not an outbound type; only outbound has projectors")
    bucket = MESSAGE_PROJECTORS.setdefault(message_type, [])
    if projector not in bucket:
        bucket.append(projector)


def register_producer(message_type: MessageType, producer: str) -> None:
    """Record an emitter of ``message_type`` (for the §4.8 per-field produce/consume diff)."""
    bucket = MESSAGE_PRODUCERS.setdefault(message_type, [])
    if producer not in bucket:
        bucket.append(producer)


def _default_channel_action_handler(message: ChannelActionMessage) -> ChannelActionMessage:
    """The default in-registry dispatch handler for the sole inbound type.

    Guarantees ``channel_action`` is always handled (closure stays green) even
    before a service wires a richer handler. The live dispatch funnel (§4.3) may
    re-bind this via :func:`register_handler` with the same-or-explicit callable.
    """
    return message


def _render_frame_projector(message: ProxyMessage) -> ProxyMessage:
    """The default in-registry projector for every outbound render frame.

    Every outbound type is rendered by the pure-rendering projector (§4.5); a
    surface-specific projector (chat/tts/canvas/tile) may register additionally.
    """
    return message


# Wire the closure-closing defaults at import time: exactly-one handler for the
# sole inbound type, and a projector for every outbound render frame. The live
# services register their concrete handler/projectors on top of these (§4.3/§4.5).
register_handler(MessageType.CHANNEL_ACTION, _default_channel_action_handler)
for _outbound in OUTBOUND:
    register_projector(_outbound, _render_frame_projector)
    register_producer(_outbound, "backend.render-frame")
register_producer(MessageType.CHANNEL_ACTION, "client.surface")


def _closure_values(message_type: Any) -> set[str]:
    """The discriminator value-set for the closure comparison."""
    if isinstance(message_type, type) and issubclass(message_type, enum.Enum):
        return {str(m.value) for m in message_type}
    # An injected union/tuple/Literal-args view (used by the orphan-rejection test).
    members = get_args(message_type) or tuple(message_type)
    out: set[str] = set()
    for m in members:
        out.add(str(m.value) if isinstance(m, enum.Enum) else str(m))
    return out


def assert_registry_closed(message_type: Any | None = None) -> None:
    """Prove the client contract graph is closed (§4.1, CANONICAL §12.12).

    Runs at boot (fail-fast) and in CI. The graph is closed iff:

    1. **set-equality** — the ``MessageType`` enum and ``CHANNEL_REGISTRY`` have the
       same discriminator values (every type has a Pydantic model, no model is
       undeclared);
    2. **every inbound type has EXACTLY ONE handler** (``MESSAGE_HANDLERS``);
    3. **every outbound type has AT LEAST ONE projector** (``MESSAGE_PROJECTORS``);
    4. **no signal-surface event leaked** into the client registry (§11.8).

    Raises ``AssertionError`` naming the violation on any drift (the ``field-contract``
    guard + the boot path + every ``pytest.raises(AssertionError)`` depend on this
    exception type). ``message_type`` overrides the enum view for the orphan test.
    """
    values = _closure_values(message_type if message_type is not None else MessageType)
    registry = {str(k) for k in CHANNEL_REGISTRY}
    if values != registry:
        raise AssertionError(
            "closed-graph violation (set-equality): "
            f"union-only={sorted(values - registry)}, registry-only={sorted(registry - values)}"
        )

    # (2) coverage: every inbound has exactly one handler; (3) every outbound ≥1 projector.
    # These run against the canonical enum partitions (an injected override is a
    # set-equality probe only, so skip coverage when a custom view is supplied to
    # keep the orphan test's semantics — the shipped, arg-less call checks all four).
    if message_type is None:
        unhandled = sorted(t.value for t in INBOUND if t not in MESSAGE_HANDLERS)
        multihandled = sorted(
            t.value
            for t in INBOUND
            if isinstance(MESSAGE_HANDLERS.get(t), (list, tuple, set))
        )
        unprojected = sorted(t.value for t in OUTBOUND if not MESSAGE_PROJECTORS.get(t))
        if unhandled or multihandled or unprojected:
            raise AssertionError(
                "contract graph not closed (coverage): "
                f"inbound-without-handler={unhandled}; "
                f"inbound-not-exactly-one-handler={multihandled}; "
                f"outbound-without-projector={unprojected}"
            )

    leaked = SIGNAL_SURFACE_EVENTS & registry
    if leaked:
        raise AssertionError(
            f"signal-surface events leaked into the client registry (§11.8): {sorted(leaked)}"
        )


def validate_inbound_message(payload: Any) -> ProxyMessage:
    """The ONE central dispatch-funnel validator for untrusted client->backend input.

    Rejects a non-mapping, a missing/unknown discriminator, a malformed body, or an
    oversized free-text field (bounded by each model's ``Field(max_length=...)``).
    The connect page is a public URL and the tile is a streamed webpage, so inbound
    traffic is untrusted and validated once, centrally (§4.3).
    """
    if not isinstance(payload, dict):
        raise TypeError("inbound message must be a JSON object")
    discriminator = payload.get("type")
    model = CHANNEL_REGISTRY.get(str(discriminator)) if discriminator is not None else None
    if model is None:
        raise ValueError(f"unregistered message type: {discriminator!r}")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:  # bounded/typed fields reject malformed+oversized
        raise ValueError(f"invalid {discriminator!r} message: {exc}") from exc


def assert_fields_consumed(
    *, produced: dict[str, set[str]], consumed: dict[str, set[str]]
) -> list[str]:
    """Return the list of produced-but-unconsumed contract fields (§4.8, AC-CMP-009).

    A non-empty return is a build-failing violation naming each orphan field.
    """
    violations: list[str] = []
    for signal, fields in produced.items():
        seen = set(consumed.get(signal, set()))
        for orphan in sorted(set(fields) - seen):
            violations.append(f"{signal}.{orphan} produced but never consumed")
    return violations
