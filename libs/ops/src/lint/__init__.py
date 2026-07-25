"""libs.lint — product lint checks (naming law + copy-voice guide).

Physically hosted under ``libs/ops/src`` and exposed at the dotted path
``libs.lint`` by extending the ``libs`` namespace ``__path__`` (see the repo-root
conftest). Hosting it inside an existing lib keeps the fixed six-package ``libs``
set intact — a new top-level ``libs/lint`` dir or module would either add a
seventh package or create a ``libs/__pycache__`` that AC-REPO-007 forbids.

Two build-time guards live here and run together in CI (guard parity):
  * ``naming`` — no internal component name in a user-visible string (§14).
  * ``copy_guide`` — no banned copy pattern in user-visible copy + the three
    honesty shapes exist as canonical seed strings (Doc 08 §2.1/§2.3).

The convenience re-exports are LAZY (via ``__getattr__``) so that
``python -m lint.copy_guide`` / ``python -m lint.naming`` do not pre-import the
submodule at package init — which would make runpy emit a spurious
"found in sys.modules ... prior to execution" RuntimeWarning.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .copy_guide import check_copy as check_copy
    from .copy_guide import check_honesty_shapes as check_honesty_shapes
    from .naming import check_user_visible_strings as check_user_visible_strings

__all__ = [
    "check_user_visible_strings",
    "check_copy",
    "check_honesty_shapes",
]

_LAZY = {
    "check_user_visible_strings": ".naming",
    "check_copy": ".copy_guide",
    "check_honesty_shapes": ".copy_guide",
}


def __getattr__(name: str) -> Any:
    """Lazily resolve the guard entrypoints from their submodules on first access."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'lint' has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module, __name__), name)
