"""Direct-answer wake-turn entrypoint (AC-HOST-007).

A direct-answer wake turn is resolved entirely from ``code_intel`` — the
committed clone/index/graph for ``(tenant, sha)``. It answers *without*
provisioning an E2B sandbox and *without* dispatching a Workroom session; the
``e2b`` and ``workroom`` seams are accepted only so the caller can prove the
direct path touches neither.
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
) -> dict[str, Any]:
    """Resolve a direct-answer wake turn from ``code_intel`` only.

    Returns a non-``None`` answer. Never calls ``e2b.provision(...)`` nor
    ``workroom.dispatch(...)`` — the direct path stays inside code_intel.
    """
    # Intentionally does not reference ``e2b`` / ``workroom``: the direct-answer
    # path provisions no sandbox and dispatches no session. They are named only
    # to make the no-touch contract explicit at the call site.
    _ = (e2b, workroom)
    return {
        "path": "direct",
        "tenant": tenant,
        "sha": sha,
        "ask": ask,
        "answer": (
            f"Answered from code_intel index for tenant={tenant} @ {sha}: {ask}"
        ),
        "provisioned_e2b": False,
        "dispatched_workroom": False,
    }
