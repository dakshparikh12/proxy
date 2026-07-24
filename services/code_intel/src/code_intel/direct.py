"""Direct-answer wake-turn entrypoint (AC-HOST-007).

A direct-answer wake turn is resolved entirely from ``code_intel`` — the
committed clone/index/graph for ``(tenant, sha)``. It answers *without*
provisioning an E2B sandbox and *without* dispatching a Workroom session; the
``e2b`` and ``workroom`` seams are accepted only so the caller can prove the
direct path touches neither.

This is the ``(ask, tenant, sha)``-shaped, dict-returning façade over the ONE
canonical resolver, which lives in the SAME layer at
:mod:`code_intel.direct_answer` (G4: no ``from harness`` upward import — the
harness re-exports the resolver downward, never the reverse). When a live
``code_intel`` handle (a :class:`code_intel.mcp_server.CodeIntelMCPServer` or a
:class:`code_intel.meeting.MeetingSession`) is supplied, the ask is routed
through the real structural tools and the returned ``answer`` carries a real
``file:line`` citation drawn from an actual file read at the pinned SHA — never a
graph edge and never a fixed string. With no handle it abstains honestly (Law 1)
rather than inventing a location.
"""

from __future__ import annotations

from typing import Any


def answer_direct(
    *,
    ask: str,
    tenant: str,
    sha: str,
    e2b: object,
    workroom: object,
    code_intel: Any = None,
    session: Any = None,
) -> dict[str, Any]:
    """Resolve a direct-answer wake turn from ``code_intel`` only.

    Returns a non-``None`` answer dict. Never calls ``e2b.provision(...)`` nor
    ``workroom.dispatch(...)`` — the direct path stays inside code_intel. When a
    live ``code_intel`` server / ``session`` is bound, the ``citation`` is a real
    ``file:line`` present in the pinned clone; otherwise the answer abstains.
    """
    # Delegate to the ONE canonical resolver, which lives in THIS layer
    # (code_intel composes its own tools). No upward import into harness (G4).
    from .direct_answer import answer_direct as _resolve

    handle = session if session is not None else code_intel
    answer = _resolve(
        ask=ask,
        tenant=tenant,
        sha=sha,
        e2b=e2b,
        workroom=workroom,
        session=session,
        code_intel=code_intel,
    )
    return {
        "path": "direct",
        "tenant": tenant,
        "sha": sha,
        "ask": ask,
        "answer": answer.text,
        "citation": answer.citation,
        "confidence": answer.confidence,
        "tool": answer.tool,
        "grounded": handle is not None and answer.citation is not None,
        "provisioned_e2b": answer.provisioned_e2b,
        "dispatched_workroom": answer.dispatched_workroom,
    }
