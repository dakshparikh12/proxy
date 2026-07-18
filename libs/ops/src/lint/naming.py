"""libs.lint.naming — the user-visible-string naming lint (§14 AC-CON-002).

Enforces the naming law: user-visible strings never contain internal component
names (Orchestrator / Scribe / workroom). The product and the agent are Proxy.
The lint flags any internal name that leaks into a user-visible string and exits
non-zero, so CI blocks the leak.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Internal component names that must never surface in a user-visible string.
_INTERNAL_NAMES: tuple[str, ...] = ("Orchestrator", "Scribe", "workroom")
_INTERNAL_RX = re.compile(r"\b(?:%s)\b" % "|".join(_INTERNAL_NAMES), re.IGNORECASE)


@dataclass(frozen=True)
class LintResult:
    """Result of the naming lint. ``exit_code`` is non-zero on any violation."""

    exit_code: int
    violations: tuple[str, ...] = field(default_factory=tuple)


def check_user_visible_strings(mapping: dict[str, str]) -> LintResult:
    """Flag any user-visible string that contains an internal name.

    ``mapping`` is ``{string_key: user_visible_value}``. Returns a
    :class:`LintResult` whose ``exit_code`` is non-zero when any value contains
    an internal component name, and 0 otherwise.
    """
    violations = tuple(
        key for key, value in mapping.items() if _INTERNAL_RX.search(value)
    )
    return LintResult(exit_code=1 if violations else 0, violations=violations)
