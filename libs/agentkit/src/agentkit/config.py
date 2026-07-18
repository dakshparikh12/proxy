"""Typed behavior registry (AC-CMP-014). No YAML — behaviors are Python
constants registered with :func:`register`."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BehaviorConfig:
    """A behavior's static capability surface (typed, immutable)."""

    name: str
    system_prompt: str = ""
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()


_REGISTRY: dict[str, BehaviorConfig] = {}


def register(config: BehaviorConfig) -> BehaviorConfig:
    """Register a behavior config by name and return it."""
    _REGISTRY[config.name] = config
    return config


def get_behavior(name: str) -> BehaviorConfig | None:
    return _REGISTRY.get(name)
