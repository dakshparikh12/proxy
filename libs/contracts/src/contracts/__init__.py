"""libs.contracts — all wire types shared across services (single home)."""
from __future__ import annotations

from .bundle import Bundle as Bundle
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
    assert_fields_consumed as assert_fields_consumed,
)
from .registry import (
    assert_registry_closed as assert_registry_closed,
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
    "CHANNEL_REGISTRY",
    "INBOUND",
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
    "assert_fields_consumed",
    "assert_registry_closed",
    "register_handler",
    "register_producer",
    "register_projector",
]
