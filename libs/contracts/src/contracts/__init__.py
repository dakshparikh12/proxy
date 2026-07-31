"""libs.contracts — all wire types shared across services (single home)."""
from __future__ import annotations

from . import channel as channel  # noqa: F401 — import fires §4.2/§4.5 model registration
from .bundle import Bundle as Bundle
from .channel import (
    ActionSurface as ActionSurface,
)
from .channel import (
    CanvasPatch as CanvasPatch,
)
from .channel import (
    ChannelAction as ChannelAction,
)
from .channel import (
    DraftCard as DraftCard,
)
from .channel import (
    NoteLine as NoteLine,
)
from .channel import (
    ResponseChunk as ResponseChunk,
)
from .channel import (
    ResponseEnd as ResponseEnd,
)
from .channel import (
    ResponseStart as ResponseStart,
)
from .channel import (
    Surface as Surface,
)
from .channel import (
    TileState as TileState,
)
from .channel import (
    ToolStart as ToolStart,
)
from .channel import (
    VoiceSpeak as VoiceSpeak,
)
from .channels import ChannelReport as ChannelReport
from .chunks import (
    AGENT_CHUNK_METADATA_KEYS as AGENT_CHUNK_METADATA_KEYS,
)
from .chunks import (
    AgentChunk as AgentChunk,
)
from .chunks import (
    ChunkType as ChunkType,
)
from .envelopes import (
    Envelope as Envelope,
)
from .envelopes import (
    EnvelopeStatus as EnvelopeStatus,
)
from .envelopes import (
    ProgressEvent as ProgressEvent,
)
from .material_change import MaterialChangeKind as MaterialChangeKind
from .notes import NoteDelta as NoteDelta
from .notes import NoteOp as NoteOp
from .readiness import Readiness as Readiness
from .readiness import ReadinessReport as ReadinessReport
from .registry import (
    CHANNEL_REGISTRY as CHANNEL_REGISTRY,
)
from .registry import (
    INBOUND as INBOUND,
)
from .registry import (
    MESSAGE_FIELD_CONSUMERS as MESSAGE_FIELD_CONSUMERS,
)
from .registry import (
    MESSAGE_FIELD_PRODUCERS as MESSAGE_FIELD_PRODUCERS,
)
from .registry import (
    MESSAGE_HANDLERS as MESSAGE_HANDLERS,
)
from .registry import (
    MESSAGE_PRODUCERS as MESSAGE_PRODUCERS,
)
from .registry import (
    MESSAGE_PROJECTORS as MESSAGE_PROJECTORS,
)
from .registry import (
    OUTBOUND as OUTBOUND,
)
from .registry import (
    SIGNAL_SURFACE_EVENTS as SIGNAL_SURFACE_EVENTS,
)
from .registry import (
    MessageType as MessageType,
)
from .registry import (
    ProxyMessage as ProxyMessage,
)
from .registry import (
    assert_contract_fields_consumed as assert_contract_fields_consumed,
)
from .registry import (
    assert_fields_consumed as assert_fields_consumed,
)
from .registry import (
    assert_registry_closed as assert_registry_closed,
)
from .registry import (
    collect_consumed_fields as collect_consumed_fields,
)
from .registry import (
    collect_produced_fields as collect_produced_fields,
)
from .registry import (
    register_field_consumer as register_field_consumer,
)
from .registry import (
    register_handler as register_handler,
)
from .registry import (
    register_producer as register_producer,
)
from .registry import (
    register_projector as register_projector,
)
from .registry import (
    validate_inbound_message as validate_inbound_message,
)

__all__ = [
    "AGENT_CHUNK_METADATA_KEYS",
    "AgentChunk",
    "Bundle",
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
    "channel",
    "CHANNEL_REGISTRY",
    "INBOUND",
    "MESSAGE_FIELD_CONSUMERS",
    "MESSAGE_FIELD_PRODUCERS",
    "MESSAGE_HANDLERS",
    "MESSAGE_PRODUCERS",
    "MESSAGE_PROJECTORS",
    "MessageType",
    "OUTBOUND",
    "validate_inbound_message",
    "ChannelReport",
    "ChunkType",
    "Envelope",
    "EnvelopeStatus",
    "MaterialChangeKind",
    "NoteDelta",
    "NoteOp",
    "ProgressEvent",
    "ProxyMessage",
    "Readiness",
    "ReadinessReport",
    "SIGNAL_SURFACE_EVENTS",
    "assert_contract_fields_consumed",
    "assert_fields_consumed",
    "assert_registry_closed",
    "collect_consumed_fields",
    "collect_produced_fields",
    "register_field_consumer",
    "register_handler",
    "register_producer",
    "register_projector",
]

# Populate ``MESSAGE_FIELD_CONSUMERS`` from the REAL consumer surface at import (the AST
# sweep of the live services + the render-frame whole-wire set) so the record is grounded
# and non-vacuous the moment the package is imported. Fail-soft: a deployed wheel has no
# source tree, so this leaves the render-frame set only and never crashes a production import.
try:
    collect_consumed_fields()
except Exception:  # noqa: BLE001 — the field-diff is a CI/test gate; never break an import
    pass
