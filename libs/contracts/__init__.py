"""libs.contracts — dotted package facade. Real code lives under src/contracts
(src-layout, AC-REPO-002); this module re-exports it at the ``libs.contracts``
import path used across the workspace."""
from __future__ import annotations

from .src.contracts import (
    AGENT_CHUNK_METADATA_KEYS as AGENT_CHUNK_METADATA_KEYS,
)
from .src.contracts import (
    CHANNEL_REGISTRY as CHANNEL_REGISTRY,
)
from .src.contracts import (
    INBOUND as INBOUND,
)
from .src.contracts import (
    MESSAGE_FIELD_CONSUMERS as MESSAGE_FIELD_CONSUMERS,
)
from .src.contracts import (
    MESSAGE_FIELD_PRODUCERS as MESSAGE_FIELD_PRODUCERS,
)
from .src.contracts import (
    MESSAGE_HANDLERS as MESSAGE_HANDLERS,
)
from .src.contracts import (
    MESSAGE_PRODUCERS as MESSAGE_PRODUCERS,
)
from .src.contracts import (
    MESSAGE_PROJECTORS as MESSAGE_PROJECTORS,
)
from .src.contracts import (
    OUTBOUND as OUTBOUND,
)
from .src.contracts import (
    SIGNAL_SURFACE_EVENTS as SIGNAL_SURFACE_EVENTS,
)
from .src.contracts import (
    ActionSurface as ActionSurface,
)
from .src.contracts import (
    AgentChunk as AgentChunk,
)
from .src.contracts import (
    Bundle as Bundle,
)
from .src.contracts import (
    CanvasPatch as CanvasPatch,
)
from .src.contracts import (
    ChannelAction as ChannelAction,
)
from .src.contracts import (
    ChannelReport as ChannelReport,
)
from .src.contracts import (
    ChunkType as ChunkType,
)
from .src.contracts import (
    DraftCard as DraftCard,
)
from .src.contracts import (
    Envelope as Envelope,
)
from .src.contracts import (
    EnvelopeStatus as EnvelopeStatus,
)
from .src.contracts import (
    MaterialChangeKind as MaterialChangeKind,
)
from .src.contracts import (
    MessageType as MessageType,
)
from .src.contracts import (
    NoteDelta as NoteDelta,
)
from .src.contracts import (
    NoteLine as NoteLine,
)
from .src.contracts import (
    NoteOp as NoteOp,
)
from .src.contracts import (
    ProgressEvent as ProgressEvent,
)
from .src.contracts import (
    ProxyMessage as ProxyMessage,
)
from .src.contracts import (
    Readiness as Readiness,
)
from .src.contracts import (
    ReadinessReport as ReadinessReport,
)
from .src.contracts import (
    ResponseChunk as ResponseChunk,
)
from .src.contracts import (
    ResponseEnd as ResponseEnd,
)
from .src.contracts import (
    ResponseStart as ResponseStart,
)
from .src.contracts import (
    Surface as Surface,
)
from .src.contracts import (
    TileState as TileState,
)
from .src.contracts import (
    ToolStart as ToolStart,
)
from .src.contracts import (
    VoiceSpeak as VoiceSpeak,
)
from .src.contracts import (
    assert_contract_fields_consumed as assert_contract_fields_consumed,
)
from .src.contracts import (
    assert_fields_consumed as assert_fields_consumed,
)
from .src.contracts import (
    assert_registry_closed as assert_registry_closed,
)
from .src.contracts import channel as channel  # re-export the §4.2/§4.5 model module
from .src.contracts import (
    collect_produced_fields as collect_produced_fields,
)
from .src.contracts import (
    register_field_consumer as register_field_consumer,
)
from .src.contracts import (
    register_handler as register_handler,
)
from .src.contracts import (
    register_producer as register_producer,
)
from .src.contracts import (
    register_projector as register_projector,
)
from .src.contracts import (
    validate_inbound_message as validate_inbound_message,
)

__all__ = [
    "AGENT_CHUNK_METADATA_KEYS",
    "ActionSurface",
    "AgentChunk",
    "Bundle",
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
    "collect_produced_fields",
    "register_field_consumer",
    "register_handler",
    "register_producer",
    "register_projector",
]
