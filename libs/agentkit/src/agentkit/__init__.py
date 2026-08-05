"""libs.agentkit — provider seam: the delta-izer, guardrails, and the Claude provider (map-build).

(The old behavior-runner/execution machinery — BehaviorRunner/render_prompt/render_role — was
deleted in the workroom pivot; native Claude runs the loop now, no host-side behavior runner. The
config/abort/resume host-side helpers were likewise retired when the warm in-sandbox session became
the single delivery path — no host code drives those seams anymore. ``deltas.stream_deltas`` remains:
it is the typed ``AgentChunk`` consumer the map-build stream reads through, and the one that keeps the
AgentChunk contract closed under the §4.8 field-diff guard.)
"""
from __future__ import annotations

from .deltas import stream_deltas as stream_deltas
from .guardrails import INJECTION_GUARDRAIL_MARK as INJECTION_GUARDRAIL_MARK
from .guardrails import injection_guardrail_suffix as injection_guardrail_suffix
from .guardrails import with_injection_guardrail as with_injection_guardrail
from .guardrails import with_proxy_guardrails as with_proxy_guardrails
from .provider import Provider as Provider
from .provider import ProviderError as ProviderError
from .provider import ProviderQuery as ProviderQuery
from .sdk_provider import ClaudeAgentProvider as ClaudeAgentProvider
from .sdk_provider import build_sdk_options as build_sdk_options
from .sdk_provider import make_map_provider as make_map_provider

__all__ = [
    "INJECTION_GUARDRAIL_MARK",
    "ClaudeAgentProvider",
    "Provider",
    "ProviderError",
    "ProviderQuery",
    "build_sdk_options",
    "make_map_provider",
    "injection_guardrail_suffix",
    "stream_deltas",
    "with_injection_guardrail",
    "with_proxy_guardrails",
]
