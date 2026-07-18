"""libs.lint — product lint checks (the user-visible-string naming law).

Physically hosted under ``libs/ops/src`` and exposed at the dotted path
``libs.lint`` by extending the ``libs`` namespace ``__path__`` (see the repo-root
conftest). Hosting it inside an existing lib keeps the fixed six-package ``libs``
set intact — a new top-level ``libs/lint`` dir or module would either add a
seventh package or create a ``libs/__pycache__`` that AC-REPO-007 forbids.
"""
from __future__ import annotations

from .naming import check_user_visible_strings as check_user_visible_strings

__all__ = ["check_user_visible_strings"]
