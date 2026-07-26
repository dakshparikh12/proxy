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
from typing import Any, Callable, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

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


# ── the message BODIES live in ``channel.py`` (§4.2/§4.5), not here ────────────
# ``registry.py`` owns only the machinery — the ``MessageType`` enum, the
# ``ProxyMessage`` base, the closure, and the produce/consume maps. The concrete
# models (``ChannelAction`` + the render frames) are defined in ``contracts.channel``
# so a shared type is described ONCE, in one place (CANONICAL §11.5). ``contracts``'s
# ``__init__`` imports that module, firing ``__pydantic_init_subclass__`` on each
# model and populating ``CHANNEL_REGISTRY`` before ``assert_registry_closed`` runs.


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

# ── the per-FIELD produce/consume registries (§4.8, CANONICAL §11.11 — un-trimmed) ──
# The type-level maps above answer "who emits this message"; these answer the finer
# question the field-diff needs: "which FIELDS of each contract model does anyone
# actually read." ``MESSAGE_FIELD_PRODUCERS`` is populated by walking the REAL Pydantic
# models (``collect_produced_fields`` below) — never a hand-list, which would itself
# drift. ``MESSAGE_FIELD_CONSUMERS`` is populated by the live consumers naming, at
# import, each field they read (``register_field_consumer``). The gate
# (``assert_contract_fields_consumed``) diffs the two and NAMES every orphan — a field
# produced by the model but read by no consumer, OR read under a name no model produces.
# This is what catches the three drifts this project already paid for: AgentChunk
# ``.kind``→``.type``, the envelope ``verified|draft``→``EnvelopeStatus``, ``dm``→``dm_available``.
MESSAGE_FIELD_PRODUCERS: dict[str, set[str]] = {}  # contract model name → the fields it PRODUCES
MESSAGE_FIELD_CONSUMERS: dict[str, set[str]] = {}  # contract model name → the fields anyone CONSUMES


def register_handler(
    message_type: MessageType, handler: Handler, *, replace: bool = False
) -> None:
    """Bind the single handler for an inbound client type (§4.3 dispatch funnel).

    Re-binding the SAME callable is idempotent; binding a *different* handler to an
    already-handled inbound type is a wiring error (exactly-one-handler invariant) —
    UNLESS ``replace=True``, which is the sanctioned way the LIVE host swaps the
    closure-closing default stub (:func:`_default_channel_action_handler`) for the real
    capability-gated handler at app-build time. Either way the type still has EXACTLY
    ONE handler afterwards (the invariant is preserved, not relaxed — the map is
    overwritten, never appended to), so closure stays green.
    """
    if message_type not in INBOUND:
        raise ValueError(f"{message_type.value!r} is not an inbound type; only inbound has handlers")
    existing = MESSAGE_HANDLERS.get(message_type)
    if existing is not None and existing is not handler and not replace:
        raise ValueError(
            f"inbound {message_type.value!r} already handled by {existing!r}; "
            "an inbound type has EXACTLY ONE handler (§4.1) — pass replace=True to swap "
            "the closure-closing default for the live handler"
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


def register_field_consumer(model_name: str, *fields: str) -> None:
    """Record that a live consumer reads ``fields`` of the contract model ``model_name``.

    The field-level half of the produce/consume graph (§4.8, CANONICAL §11.11). Idempotent
    and additive — re-registering the same field is a no-op.

    **This is NO LONGER the source of truth for the standalone-contract diff.** The real
    consumer field-reads for ``AgentChunk`` / ``Envelope`` / ``ChannelReport`` are DERIVED
    by AST-sweeping the live service source (:func:`collect_consumed_fields` →
    ``contracts.contract_reads.sweep_consumer_reads``), because a hand-list co-located with
    the model is a tautology (``model_fields == model_fields``): it cannot fail when a real
    consumer renames ``chunk.type``→``chunk.kind``. This API remains for the render-frame
    WHOLE-WIRE consumption (a registered ``ProxyMessage`` is serialized whole by ``send()``,
    so its entire declared field-set genuinely is consumed on the wire — that is a true fact
    about the frame, not a restatement of its fields) and for tests that drive the primitive.
    """
    bucket = MESSAGE_FIELD_CONSUMERS.setdefault(model_name, set())
    bucket.update(fields)


# ── the produce/consume field-diff ALLOW-LIST (DoD: "empty ... or explicitly allow-listed") ──
# A produced contract field that is deliberately NOT read off the model by any consumer
# attribute access — it is threaded through by CONSTRUCTION and correlated out-of-band, so
# the AST sweep (which records attribute READS) will never see it and would otherwise flag
# it as a produced-but-unconsumed orphan. Each entry names the field AND why it is exempt;
# the gate still flags every OTHER orphan, so this is a narrow, audited carve-out, not a rug.
_FIELD_DIFF_ALLOWLIST: frozenset[str] = frozenset(
    {
        # The 05→04 result Envelope's task_id is a CORRELATION key: it is set at construction
        # from ``bundle.task_id`` (workroom/envelope.py) and matched against the operation-runs
        # row / abort-controller key (``meeting_id|task_id``, session.py) — never read as
        # ``envelope.task_id`` off the model. It is consumed by identity, not by field-read.
        "Envelope.task_id",
    }
)


def _default_channel_action_handler(message: ProxyMessage) -> ProxyMessage:
    """The default in-registry dispatch handler for the sole inbound type.

    Typed against the ``ProxyMessage`` base (the concrete ``ChannelAction`` body lives
    in ``contracts.channel`` — importing it here would be circular). Guarantees
    ``channel_action`` is always handled (closure stays green) even before a service
    wires a richer handler. The live dispatch funnel (§4.3) may re-bind this via
    :func:`register_handler` with the same-or-explicit callable.
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


# ── the field-diff CONTRACT MODELS the graph walks ─────────────────────────────
# The shared wire/behavior contracts whose FIELDS the produce/consume diff covers.
# These are the models that already proved field-level drift (§4.8): the AgentChunk
# streaming union, the Workroom result Envelope, and the channel-report signal — plus
# every registered client ``ProxyMessage`` render frame. Listed by dotted path so the
# collector walks the REAL Pydantic ``model_fields`` and never a hand-maintained field
# list (which would itself drift — the very failure this gate exists to prevent).
_FIELD_DIFF_CONTRACT_MODELS: tuple[tuple[str, str], ...] = (
    ("contracts.chunks", "AgentChunk"),
    ("contracts.envelopes", "Envelope"),
    ("contracts.channels", "ChannelReport"),
)


def collect_produced_fields() -> dict[str, set[str]]:
    """Populate the produced-field graph by walking the REAL contract models (§4.8).

    Returns ``{model_name: {field, ...}}`` read straight off each live Pydantic model's
    ``model_fields`` — for the standalone contracts (AgentChunk / Envelope / ChannelReport)
    AND for every registered client ``ProxyMessage`` render frame in ``CHANNEL_REGISTRY``.
    A field a model no longer carries (the migrated ``.kind``, ``dm``, bare ``verified``)
    is absent BY CONSTRUCTION, so a consumer still reading it shows up as an orphan.

    The result is also written into ``MESSAGE_FIELD_PRODUCERS`` so the registry mirrors
    the live model shape (single source of truth — CANONICAL §11.5).
    """
    import importlib

    produced: dict[str, set[str]] = {}

    for module_path, model_name in _FIELD_DIFF_CONTRACT_MODELS:
        module = importlib.import_module(module_path)
        model = getattr(module, model_name)
        produced[model_name] = set(model.model_fields)

    # every registered render frame / channel_action contributes its produced fields too.
    for model in CHANNEL_REGISTRY.values():
        produced[model.__name__] = set(model.model_fields)

    MESSAGE_FIELD_PRODUCERS.clear()
    MESSAGE_FIELD_PRODUCERS.update({name: set(fields) for name, fields in produced.items()})
    return produced


def collect_consumed_fields() -> dict[str, set[str]]:
    """Populate the consumed-field graph from the REAL consumer source (§4.8) — no hand-list.

    Two grounded sources, unioned into ``MESSAGE_FIELD_CONSUMERS``:

    1. **AST sweep of the live services** — for the standalone contracts
       (``AgentChunk`` / ``Envelope`` / ``ChannelReport``) this walks every
       ``services/*/src`` + ``libs/*/src`` module and records which fields the product code
       actually READS off a contract-typed value (``contracts.contract_reads``). A consumer
       renaming ``chunk.type``→``chunk.kind`` now surfaces ``AgentChunk.kind`` as
       consumed-but-never-produced — the diff FAILS on the real path, which the old hand-list
       could not do.
    2. **Render-frame whole-wire consumption** — every registered ``ProxyMessage`` is
       serialized whole by ``send()`` (``model_dump()``) onto the surface wire, so its entire
       field-set genuinely is consumed. Registered by walking each frame's ``model_fields``.

    Fail-soft: in a DEPLOYED wheel the source tree is absent, so the sweep returns nothing and
    only the whole-wire frames populate — the gate is a CI/test-time check and never crashes a
    production import. Returns ``{model_name: {consumed_field, ...}}`` and also writes it into
    ``MESSAGE_FIELD_CONSUMERS`` (the record the doc-08 acceptance reads).
    """
    from .contract_reads import sweep_consumer_reads

    consumed: dict[str, set[str]] = {}

    # (1) the real, swept consumer reads for the standalone contracts.
    try:
        swept = sweep_consumer_reads()
    except Exception:  # noqa: BLE001 — a missing/unreadable source tree must not crash import
        swept = {}
    for model_name, fields in swept.items():
        consumed.setdefault(model_name, set()).update(fields)

    # (2) every registered render frame is consumed WHOLE on the wire (model_dump()).
    for model in CHANNEL_REGISTRY.values():
        consumed.setdefault(model.__name__, set()).update(model.model_fields)

    MESSAGE_FIELD_CONSUMERS.clear()
    MESSAGE_FIELD_CONSUMERS.update({name: set(fields) for name, fields in consumed.items()})
    return consumed


def assert_contract_fields_consumed(
    *,
    produced: dict[str, set[str]] | None = None,
    consumed: dict[str, set[str]] | None = None,
    strict: bool = False,
) -> list[str]:
    """The §4.8 / CANONICAL §11.11 per-FIELD produce/consume gate — the un-trimmed field-diff.

    Beyond the §4.1 set-equality (type-registered ↔ model-exists), this walks each
    contract model's real fields and flags any field **produced by the model but read
    by no consumer**, OR **read by a consumer under a name the model never produces**.
    Both directions are named — that is what catches the drift class this project
    already paid for (AgentChunk ``.kind``→``.type``, envelope ``verified|draft``→
    ``EnvelopeStatus``, ``dm``→``dm_available``): a rename orphans the OLD name on the
    consumer side and the NEW name on the producer side, and both appear in the report.

    ``produced`` defaults to the live models (:func:`collect_produced_fields`);
    ``consumed`` defaults to the REAL consumer surface (:func:`collect_consumed_fields` —
    the AST sweep of the live services + the render-frame whole-wire set), NOT a hand-list.
    A produced field on the narrow, documented ``_FIELD_DIFF_ALLOWLIST`` (a construction-
    threaded correlation key no consumer reads by attribute) is exempt from the
    produced-but-unconsumed direction only. ``strict=True`` RAISES ``AssertionError`` (naming
    every orphan) instead of returning the list — the form the CI/boot gate uses to FAIL THE BUILD.
    """
    if produced is None:
        produced = collect_produced_fields()
    if consumed is None:
        consumed = collect_consumed_fields()

    # produced-but-never-consumed (the shipped one-directional primitive) — minus the
    # explicitly allow-listed construction-threaded correlation keys (DoD: "or allow-listed").
    violations = [
        v
        for v in assert_fields_consumed(produced=produced, consumed=consumed)
        if v.split(" ", 1)[0] not in _FIELD_DIFF_ALLOWLIST
    ]

    # consumed-but-never-produced (the reverse orphan — a field read under a name no
    # model produces; this is the OLD-name half of every rename drift). NEVER allow-listed:
    # a consumer reading a name the model does not carry is always real drift.
    for model_name, fields in consumed.items():
        produced_fields = set(produced.get(model_name, set()))
        for orphan in sorted(set(fields) - produced_fields):
            violations.append(f"{model_name}.{orphan} consumed but never produced")

    violations.sort()
    if strict and violations:
        raise AssertionError(
            "contract field-diff not closed (§4.8 / CANONICAL §11.11) — "
            "each field is produced by one side and consumed by neither:\n  "
            + "\n  ".join(violations)
        )
    return violations
