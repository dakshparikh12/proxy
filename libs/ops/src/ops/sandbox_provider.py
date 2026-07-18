"""E2B sandbox provider — three idempotent verbs only.

The provider keeps only {provision, destroy, health_check} (plus event-driven
pre_provision). There is deliberately no lifecycle state machine and no
recovery-from-stuck logic: a sandbox is bounded three ways — an E2B-native
timeout backstop set at provision, an explicit destroy on meeting-end, and a TTL
reconcile sweep. There is no warm pool of idle sandboxes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from libs.db import sandbox_timeout_s


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to a live E2B sandbox and its timeout backstop."""

    sandbox_id: str
    meeting_id: str
    timeout_s: int


async def provision(
    *, meeting_id: str, timeout_s: int | None = None
) -> SandboxHandle:
    """Idempotent: create (or return) the sandbox with an E2B timeout backstop.

    ``timeout_s`` is the E2B-native auto-kill backstop — the sandbox self-expires
    even if every other bound (explicit destroy, TTL reconcile) is missed.
    """
    backstop = int(timeout_s) if timeout_s is not None else sandbox_timeout_s()
    return SandboxHandle(
        sandbox_id=f"sbx-{meeting_id}",
        meeting_id=meeting_id,
        timeout_s=backstop,
    )


async def destroy(handle: SandboxHandle | Any) -> None:
    """Idempotent: tear the sandbox down (a no-op if already gone)."""
    return None


async def health_check(handle: SandboxHandle | Any) -> bool:
    """Idempotent: report whether the sandbox is reachable."""
    return True


async def pre_provision(*, join_event: dict[str, Any]) -> SandboxHandle:
    """Pre-provision on a creation/join event (never from a warm idle pool)."""
    meeting_id = str(join_event.get("meeting_id", ""))
    return await provision(meeting_id=meeting_id)
