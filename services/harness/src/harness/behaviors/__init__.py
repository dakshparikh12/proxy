"""``harness.behaviors`` — Proxy's wake-behaviors as typed ``BehaviorConfig``
constants (§3.4; D-014 model-seat mapping, D-015 curated tool subsets, D-016 field
set, D-023 no per-behavior branch).

Each behavior is a typed Python constant declared in its own module and registered
with **one ``register()`` line** — NO YAML loader (CANONICAL §12.5). Importing this
package imports every behavior module, so each ``register()`` fires and the
:data:`REGISTRY` below is populated by name. The generic
:class:`~agentkit.execution.BehaviorRunner` reads a constant and never branches per
behavior — selecting a behavior by name IS the branch (D-023).

A small **capability manifest** (:func:`capability_manifest`) is generated at build
from the SAME ``config.tools`` list each behavior declares — the one place the
capability surface crosses into JSON/TS (for UI labels). It never re-derives the
tool set; it reads it straight off the typed constant.

Adding a behavior is **one constant + one ``register()`` line** in a new module,
plus its import here — never a new code branch, never new wiring.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentkit import Behavior

from .answer_question import ANSWER_QUESTION
from .catchup import CATCHUP
from .propose_action import PROPOSE_ACTION
from .surface_risk import SURFACE_RISK

# The wake-behaviors, in declaration order. Adding a behavior appends one constant.
_BEHAVIORS: tuple[Behavior, ...] = (
    ANSWER_QUESTION,
    CATCHUP,
    SURFACE_RISK,
    PROPOSE_ACTION,
)

# The name→Behavior registry the runner is handed (selecting a name IS the branch).
REGISTRY: dict[str, Behavior] = {b.name: b for b in _BEHAVIORS}


def get_behavior(name: str) -> Behavior | None:
    """Return the registered wake-behavior by name, or ``None``."""
    return REGISTRY.get(name)


def all_behaviors() -> tuple[Behavior, ...]:
    """Return every registered wake-behavior in declaration order."""
    return _BEHAVIORS


def capability_manifest() -> dict[str, dict[str, object]]:
    """Build the JSON/TS capability manifest from the SAME ``config.tools`` list.

    One entry per behavior, keyed by name; each carries the curated ``tools`` list
    (verbatim from the typed constant — never a re-derivation), the model seat's
    resolved id, the turn budget, and the input keys. This is the sole crossing of
    the capability surface into JSON for UI labels (§3.4).
    """
    manifest: dict[str, dict[str, object]] = {}
    for b in _BEHAVIORS:
        c = b.config
        manifest[b.name] = {
            "name": b.name,
            "tools": list(c.tools),          # the curated subset, verbatim
            "model": c.model,
            "max_turns": c.max_turns,
            "inputs": list(c.inputs),
        }
    return manifest


def write_capability_manifest(path: str | Path) -> Path:
    """Emit the capability manifest as JSON to ``path`` at build time; return it."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(capability_manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


__all__ = [
    "ANSWER_QUESTION",
    "CATCHUP",
    "PROPOSE_ACTION",
    "REGISTRY",
    "SURFACE_RISK",
    "all_behaviors",
    "capability_manifest",
    "get_behavior",
    "write_capability_manifest",
]
