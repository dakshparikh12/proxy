"""libs.agentkit facade (src-layout; real code under src/agentkit)."""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so real submodules
# (``libs.agentkit.tools`` — the tool-handler registry) resolve as genuine
# importable modules. Mirrors the proven ``libs.ops`` / ``libs.http`` pattern.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "agentkit")]

from .src.agentkit import AbortController as AbortController
from .src.agentkit import AbortRegistry as AbortRegistry
from .src.agentkit import Behavior as Behavior
from .src.agentkit import BehaviorConfig as BehaviorConfig
from .src.agentkit import BehaviorRunner as BehaviorRunner
from .src.agentkit import Provider as Provider
from .src.agentkit import ProviderError as ProviderError
from .src.agentkit import ProviderQuery as ProviderQuery
from .src.agentkit import compute_builtin_tools as compute_builtin_tools
from .src.agentkit import get_behavior as get_behavior
from .src.agentkit import pick_provider as pick_provider
from .src.agentkit import register as register
from .src.agentkit import register_provider as register_provider
from .src.agentkit import render_prompt as render_prompt
from .src.agentkit import render_role as render_role
from .src.agentkit import resume_with_fallback as resume_with_fallback
from .src.agentkit import stream_deltas as stream_deltas
from .src.agentkit import thinking_policy as thinking_policy
from .src.agentkit import with_proxy_guardrails as with_proxy_guardrails

__all__ = [
    "AbortController",
    "AbortRegistry",
    "Behavior",
    "BehaviorConfig",
    "BehaviorRunner",
    "Provider",
    "ProviderError",
    "ProviderQuery",
    "compute_builtin_tools",
    "get_behavior",
    "pick_provider",
    "register",
    "register_provider",
    "render_prompt",
    "render_role",
    "resume_with_fallback",
    "stream_deltas",
    "thinking_policy",
    "with_proxy_guardrails",
]
