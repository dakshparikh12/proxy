"""Typed behavior registry (AC-CMP-014). No YAML — behaviors are Python
constants registered with :func:`register`.

``BehaviorConfig`` is the single typed capability *envelope* a behavior declares
(D-016): which curated tools it may touch, which model tier and turn budget it
runs on, which role/rules prime the turn, and which inputs it needs. The generic
:class:`~agentkit.execution.BehaviorRunner` reads this constant — it does NOT
branch per behavior. *Config configures the capability surface; model judgment
makes the choices* (§3.4): the ``tools`` list says WHAT a behavior may use, never
WHAT to do with a given utterance.

The field set is additive over the original ``{name, system_prompt,
allowed_tools, disallowed_tools}`` so the sealed AC-CMP-014 oracle (typed config
+ ``register``) stays green while the D-016 envelope (``tools``/``model``/
``role``/``max_turns``/``rules``/``inputs``) is available to the runner.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BehaviorConfig:
    """A behavior's static capability surface (typed, immutable) — the D-016 envelope.

    ``tools`` is the curated subset the runner mounts as ``allowed_tools`` (§10.5,
    never the union). ``allowed_tools`` is kept as an accepted alias for the same
    surface; when ``tools`` is empty it falls back to ``allowed_tools`` so either
    field name populates the mounted set.
    """

    name: str
    # D-016 envelope
    tools: tuple[str, ...] = ()
    model: str = ""
    role: str = ""
    max_turns: int = 1
    rules: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    # Retained originals (AC-CMP-014 + workroom disallowed-tool policy)
    system_prompt: str = ""
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()

    @property
    def mounted_tools(self) -> tuple[str, ...]:
        """The curated tool subset the runner mounts as ``allowed_tools``.

        Prefers the D-016 ``tools`` field; falls back to the legacy
        ``allowed_tools`` so both authoring styles resolve to one subset. NEVER
        the union of the two — a behavior declares exactly one curated set.
        """
        return self.tools or self.allowed_tools


@dataclass(frozen=True)
class Behavior:
    """A wake-behavior / Workroom disposition: its config envelope + prompt priming.

    Selecting a behavior by name IS the branch (§3.4 / D-023) — there is no
    per-behavior code path. The ``role`` + ``rules`` are prompt mental-models that
    prime judgment, not a decision table.
    """

    name: str
    config: BehaviorConfig
    role: str = ""
    rules: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()


_REGISTRY: dict[str, BehaviorConfig] = {}


def register(config: BehaviorConfig) -> BehaviorConfig:
    """Register a behavior config by name and return it."""
    _REGISTRY[config.name] = config
    return config


def get_behavior(name: str) -> BehaviorConfig | None:
    return _REGISTRY.get(name)
