"""libs.agentkit — provider seam: config, delta-izer, guardrails, provider, abort/resume.

(The old behavior-runner/execution machinery — BehaviorRunner/render_prompt/render_role — was
deleted in the workroom pivot; native Claude runs the loop now, no host-side behavior runner.)
"""
from __future__ import annotations

from .abort import AbortController as AbortController
from .abort import AbortRegistry as AbortRegistry
from .config import Behavior as Behavior
from .config import BehaviorConfig as BehaviorConfig
from .config import get_behavior as get_behavior
from .config import register as register
from .deltas import stream_deltas as stream_deltas
from .guardrails import INJECTION_GUARDRAIL_MARK as INJECTION_GUARDRAIL_MARK
from .guardrails import injection_guardrail_suffix as injection_guardrail_suffix
from .guardrails import with_injection_guardrail as with_injection_guardrail
from .guardrails import with_proxy_guardrails as with_proxy_guardrails
from .provider import Provider as Provider
from .provider import ProviderError as ProviderError
from .provider import ProviderQuery as ProviderQuery
from .provider import compute_builtin_tools as compute_builtin_tools
from .provider import pick_provider as pick_provider
from .provider import register_provider as register_provider
from .provider import thinking_policy as thinking_policy
from .resume import resume_with_fallback as resume_with_fallback

__all__ = [
    "INJECTION_GUARDRAIL_MARK",
    "AbortController",
    "AbortRegistry",
    "Behavior",
    "BehaviorConfig",
    "Provider",
    "ProviderError",
    "ProviderQuery",
    "compute_builtin_tools",
    "get_behavior",
    "injection_guardrail_suffix",
    "pick_provider",
    "register",
    "register_provider",
    "resume_with_fallback",
    "stream_deltas",
    "thinking_policy",
    "with_injection_guardrail",
    "with_proxy_guardrails",
]
