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
    SIGNAL_SURFACE_EVENTS as SIGNAL_SURFACE_EVENTS,
)
from .src.contracts import (
    AgentChunk as AgentChunk,
)
from .src.contracts import (
    Bundle as Bundle,
)
from .src.contracts import (
    ChannelReport as ChannelReport,
)
from .src.contracts import (
    ChunkType as ChunkType,
)
from .src.contracts import (
    Envelope as Envelope,
)
from .src.contracts import (
    EnvelopeStatus as EnvelopeStatus,
)
from .src.contracts import (
    NoteDelta as NoteDelta,
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
    assert_fields_consumed as assert_fields_consumed,
)
from .src.contracts import (
    assert_registry_closed as assert_registry_closed,
)

__all__ = [
    "AGENT_CHUNK_METADATA_KEYS",
    "AgentChunk",
    "Bundle",
    "CHANNEL_REGISTRY",
    "ChannelReport",
    "ChunkType",
    "Envelope",
    "EnvelopeStatus",
    "NoteDelta",
    "NoteOp",
    "ProgressEvent",
    "ProxyMessage",
    "Readiness",
    "ReadinessReport",
    "SIGNAL_SURFACE_EVENTS",
    "assert_fields_consumed",
    "assert_registry_closed",
]
