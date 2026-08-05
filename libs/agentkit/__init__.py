"""libs.agentkit facade (src-layout; real code under src/agentkit)."""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so real submodules
# resolve as genuine importable modules. Mirrors the proven ``libs.ops`` / ``libs.http`` pattern.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "agentkit")]

from .src.agentkit import INJECTION_GUARDRAIL_MARK as INJECTION_GUARDRAIL_MARK
from .src.agentkit import Provider as Provider
from .src.agentkit import ProviderError as ProviderError
from .src.agentkit import ProviderQuery as ProviderQuery
from .src.agentkit import injection_guardrail_suffix as injection_guardrail_suffix
from .src.agentkit import stream_deltas as stream_deltas
from .src.agentkit import with_injection_guardrail as with_injection_guardrail
from .src.agentkit import with_proxy_guardrails as with_proxy_guardrails

__all__ = [
    "INJECTION_GUARDRAIL_MARK",
    "Provider",
    "ProviderError",
    "ProviderQuery",
    "injection_guardrail_suffix",
    "stream_deltas",
    "with_injection_guardrail",
    "with_proxy_guardrails",
]
