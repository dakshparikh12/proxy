"""libs.agentkit facade (src-layout; real code under src/agentkit)."""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so real submodules
# (``libs.agentkit.tools`` — the tool-handler registry) resolve as genuine
# importable modules. Mirrors the proven ``libs.ops`` / ``libs.http`` pattern.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "agentkit")]

from .src.agentkit import AbortRegistry as AbortRegistry
from .src.agentkit import BehaviorConfig as BehaviorConfig
from .src.agentkit import BehaviorRunner as BehaviorRunner
from .src.agentkit import get_behavior as get_behavior
from .src.agentkit import register as register
from .src.agentkit import resume_with_fallback as resume_with_fallback
from .src.agentkit import stream_deltas as stream_deltas

__all__ = [
    "AbortRegistry",
    "BehaviorConfig",
    "BehaviorRunner",
    "get_behavior",
    "register",
    "resume_with_fallback",
    "stream_deltas",
]
