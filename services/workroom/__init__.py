"""services.workroom — dotted package facade (real code under src/workroom).

The facade re-exports the top-level API and also extends ``__path__`` so that
dotted submodule imports (e.g. ``services.workroom.drafts``) resolve directly
to the real ``src/workroom`` package modules. The facade directory stays first
on ``__path__`` so the ``from .src.workroom import ...`` re-exports below keep
resolving ``services.workroom.src``.
"""
from __future__ import annotations

import os as _os

__path__ = [
    _os.path.dirname(__file__),
    _os.path.join(_os.path.dirname(__file__), "src", "workroom"),
]

from .src.workroom import accept_draft as accept_draft
from .src.workroom import propose_change as propose_change

__all__ = [
    "accept_draft",
    "propose_change",
]
