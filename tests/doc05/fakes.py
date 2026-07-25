"""In-process fakes for the Doc 05 Workroom host-side tests.

e2b is NOT installed and MUST NOT be. The host code path is proven against these
fakes: a fake E2B backend that mimics the confirmed live wire surface
(``AsyncSandbox.create(template, timeout, envs, metadata)`` → instance
``.kill()`` / ``.set_timeout(seconds)`` / ``.is_running()``), recording that every
outbound op is issued through the ``call_external`` seam.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from libs.http.external import ExternalCallOutcome


class FakeE2BBackend:
    """A fake E2B backend with the shape ``sandbox_provider`` drives.

    Mirrors the injected-seam contract: ``create`` / ``kill`` / ``set_timeout`` /
    ``is_running``, each issued through this fake's own ``call_external`` so the
    test can assert the host never touches a raw client.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.create_envs: dict[str, dict[str, str]] = {}
        self.killed: list[str] = []
        self.timeouts_set: dict[str, list[int]] = {}
        self.alive: dict[str, bool] = {}
        self.went_through_call_external = False

    async def call_external(
        self,
        op: Callable[[], Awaitable[Any]],
        *,
        service: str,
        unit_cost_usd: float = 0.0,
    ) -> ExternalCallOutcome:
        """Stand-in for ``libs.http.call_external`` — records that it was used."""
        self.went_through_call_external = True
        assert service == "e2b", f"external call tagged {service!r}, expected 'e2b'"
        value = await op()
        return ExternalCallOutcome(value=value, attempts=1, total_cost_usd=unit_cost_usd)

    async def create(
        self, *, sandbox_id: str, template: str, timeout: int, envs: dict[str, str], metadata: dict[str, str]
    ) -> str:
        # Route the create through call_external, exactly as the real backend does.
        async def _raw() -> str:
            self.created.append(sandbox_id)
            self.create_envs[sandbox_id] = dict(envs)
            self.timeouts_set.setdefault(sandbox_id, []).append(int(timeout))
            self.alive[sandbox_id] = True
            return sandbox_id

        outcome = await self.call_external(_raw, service="e2b")
        return str(outcome.value)

    async def kill(self, *, sandbox_id: str) -> None:
        # Tolerate an already-gone sandbox (the live SDK raises/404s; we no-op).
        async def _raw() -> None:
            self.killed.append(sandbox_id)
            self.alive[sandbox_id] = False

        await self.call_external(_raw, service="e2b")

    async def set_timeout(self, *, sandbox_id: str, timeout: int) -> None:
        async def _raw() -> None:
            self.timeouts_set.setdefault(sandbox_id, []).append(int(timeout))

        await self.call_external(_raw, service="e2b")

    async def is_running(self, *, sandbox_id: str) -> bool:
        async def _raw() -> bool:
            return self.alive.get(sandbox_id, False)

        outcome = await self.call_external(_raw, service="e2b")
        return bool(outcome.value)
