"""Single definition home for ``resume_with_fallback`` (AC-CMP-010).

Its concrete arity/signature is pinned by Doc 04/05 (A-010) — Doc 00 only fixes
that it is defined exactly once, in libs/agentkit. Do not invent an arity here.
"""
from __future__ import annotations

from typing import Any


async def resume_with_fallback(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedError(
        "resume_with_fallback arity is defined by Doc 04/05, not Doc 00"
    )
