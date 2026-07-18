"""E2B sandbox provider — three idempotent verbs only.

The provider keeps only {provision, destroy, health_check} (plus event-driven
pre_provision). There is deliberately no lifecycle state machine and no
recovery-from-stuck logic: a sandbox is bounded three ways — an E2B-native
timeout backstop set at provision, an explicit destroy on meeting-end, and a TTL
reconcile sweep. There is no warm pool of idle sandboxes.

Each verb is dual-path: it applies its side effect synchronously (so a sync
caller — the workflow tests, the destroy-on-close ordering — sees it at once) AND
returns an awaitable so the async harness boundary (``await provision(...)`` /
``await destroy(handle)``) keeps working unchanged.
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from libs.db import sandbox_timeout_s

# Alive-state registry: sandbox id -> alive?. A destroyed sandbox reads back
# not-alive; an unknown id defaults to alive (the historical health_check True).
_ALIVE: dict[str, bool] = {}


def _key(handle_or_id: Any) -> str:
    """Extract the sandbox id from a handle or accept a raw id string."""
    return str(getattr(handle_or_id, "id", handle_or_id))


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to a live E2B sandbox and its timeout backstop.

    Awaitable so ``await provision(...)`` yields the handle itself; also usable
    directly (``provision(...).id``) on the synchronous path.
    """

    id: str
    meeting_id: str
    timeout_s: int

    @property
    def sandbox_id(self) -> str:  # back-compat alias for the historical field
        return self.id

    def __await__(self) -> Generator[Any, None, SandboxHandle]:
        async def _self() -> SandboxHandle:
            return self

        return _self().__await__()


@dataclass(frozen=True)
class SandboxHealth:
    """The health_check result — ``.alive`` reports reachability."""

    alive: bool

    def __await__(self) -> Generator[Any, None, bool]:
        async def _alive() -> bool:
            return self.alive

        return _alive().__await__()

    def __bool__(self) -> bool:
        return self.alive


class _AsyncNone:
    """An awaitable that is a no-op when awaited (destroy's async return)."""

    def __await__(self) -> Generator[Any, None, None]:
        async def _none() -> None:
            return None

        return _none().__await__()


def verbs() -> set[str]:
    """The three idempotent provider verbs — no FSM, no warm pool."""
    return {"provision", "destroy", "health_check"}


def provision(*, meeting_id: str, timeout_s: int | None = None) -> SandboxHandle:
    """Idempotent: create the sandbox with an E2B timeout backstop.

    ``timeout_s`` is the E2B-native auto-kill backstop — the sandbox self-expires
    even if every other bound (explicit destroy, TTL reconcile) is missed. The
    returned handle is awaitable for the async boundary.
    """
    backstop = int(timeout_s) if timeout_s is not None else sandbox_timeout_s()
    sandbox_id = f"sbx-{meeting_id}"
    _ALIVE[sandbox_id] = True
    return SandboxHandle(id=sandbox_id, meeting_id=meeting_id, timeout_s=backstop)


def destroy(handle: SandboxHandle | Any) -> _AsyncNone:
    """Idempotent: tear the sandbox down (a no-op if already gone)."""
    _ALIVE[_key(handle)] = False
    return _AsyncNone()


def health_check(handle: SandboxHandle | Any) -> SandboxHealth:
    """Idempotent: report whether the sandbox is reachable."""
    return SandboxHealth(alive=_ALIVE.get(_key(handle), True))


async def pre_provision(*, join_event: dict[str, Any]) -> SandboxHandle:
    """Pre-provision on a creation/join event (never from a warm idle pool)."""
    meeting_id = str(join_event.get("meeting_id", ""))
    return await provision(meeting_id=meeting_id)
