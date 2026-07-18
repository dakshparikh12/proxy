"""libs.agentkit — provider seam: behavior runner, delta-izer, abort/resume."""
from __future__ import annotations

from .abort import AbortRegistry as AbortRegistry
from .config import BehaviorConfig as BehaviorConfig
from .config import get_behavior as get_behavior
from .config import register as register
from .deltas import stream_deltas as stream_deltas
from .execution import BehaviorRunner as BehaviorRunner
from .resume import resume_with_fallback as resume_with_fallback

__all__ = [
    "AbortRegistry",
    "BehaviorConfig",
    "BehaviorRunner",
    "get_behavior",
    "register",
    "resume_with_fallback",
    "stream_deltas",
]
