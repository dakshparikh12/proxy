"""In-process fakes for the Doc 05 Workroom host-side tests.

e2b is NOT installed and MUST NOT be. The host code path is proven against these
fakes: a fake E2B backend that mimics the confirmed live wire surface
(``AsyncSandbox.create(template, timeout, envs, metadata)`` → instance
``.kill()`` / ``.set_timeout(seconds)`` / ``.is_running()``), recording that every
outbound op is issued through the ``call_external`` seam.

For the sandbox-sidecar node, :class:`FakeSidecar` mirrors the exact JWT gate the
real baked Node ``workspace-mcp-server`` sidecar enforces inside E2B on ``:8081``
(§3.5): HS256-verify the ``Authorization: Bearer <jwt>`` against THIS sandbox's
own per-sandbox secret, then the defense-in-depth per-sandbox claim check
(decoded ``session_id`` MUST equal ``env.SESSION_ID`` else 403), a boot that
``exit(1)``s on a missing ``JWT_SECRET``, and an unauth ``/health`` reporting the
baked code-hash. It lets the host-side transport prove cross-meeting rejection
(A's token → 403 on B) without the real Node sidecar (a deploy artifact).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import jwt as _jwt

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


class SidecarBootError(RuntimeError):
    """The fake sidecar's ``exit(1)`` at boot — raised when ``JWT_SECRET`` is missing.

    The real baked Node sidecar calls ``process.exit(1)`` at boot if ``env.JWT_SECRET``
    is absent (§3.5); a live sandbox whose provision failed to inject the secret is
    fail-closed rather than starting an unauthenticated tool server. The Python fake
    raises this instead of killing the test process.
    """


class FakeSidecar:
    """An in-process stand-in for the E2B-baked Node ``workspace-mcp-server`` sidecar.

    Mirrors the exact §3.5 request gate the real sidecar runs INSIDE its sandbox on
    ``:8081`` so the host-side transport can be proven end-to-end without the real
    (deploy-only) Node process:

      * boot — ``JWT_SECRET`` missing → ``exit(1)`` (:class:`SidecarBootError`).
      * ``/health`` (unauth) → 200 + the baked ``code_hash`` and clone status; no JWT.
      * every ``tools/*`` POST — HS256-verify ``Authorization: Bearer <jwt>`` against
        THIS sandbox's own secret (a token minted for another sandbox can't verify),
        then the per-sandbox claim check: decoded ``session_id`` MUST equal
        ``env.SESSION_ID`` (this sandbox's claim) else ``403``. A missing/blank header
        → ``401``.

    Each sidecar is bound to ONE sandbox's ``(jwt_secret, session_id)`` — exactly the
    per-sandbox isolation the transport relies on.
    """

    def __init__(
        self,
        *,
        jwt_secret: str,
        session_id: str,
        code_hash: str = "sha256:baked-code-hash",
        clone_ready: bool = True,
    ) -> None:
        # Boot fail-closed: a real sidecar exit(1)s if JWT_SECRET is unset (§3.5).
        if not jwt_secret:
            raise SidecarBootError("JWT_SECRET missing at boot — sidecar exit(1)")
        self._jwt_secret = jwt_secret
        self._session_id = session_id  # env.SESSION_ID — this sandbox's claim
        self._code_hash = code_hash
        self._clone_ready = clone_ready
        # A stateless per-request transport keeps NO session store (§3.5); this only
        # records tool calls so a test can assert the effect landed in THIS sandbox.
        self.executed: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        """The unauth ``GET /health`` — status 200 + baked code-hash + clone status."""
        return {
            "status": 200,
            "code_hash": self._code_hash,
            "clone_ready": self._clone_ready,
        }

    def handle_tool_call(
        self, *, authorization: str | None, tool: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Verify the JWT gate + claim check, then 'execute' the tool in THIS sandbox.

        Returns ``{"status": <code>, ...}``. A cross-meeting token (minted with
        another sandbox's secret) fails HS256 verification → 403; a same-secret token
        carrying another sandbox's ``session_id`` fails the claim check → 403.
        """
        if not authorization or not authorization.startswith("Bearer "):
            return {"status": 401, "error": "missing bearer token"}
        raw = authorization[len("Bearer ") :]
        try:
            # HS256 against THIS sandbox's secret. A token minted for another sandbox
            # (a different secret) raises InvalidSignatureError here → 403.
            claims = _jwt.decode(raw, self._jwt_secret, algorithms=["HS256"])
        except _jwt.ExpiredSignatureError:
            return {"status": 403, "error": "expired token"}
        except _jwt.InvalidTokenError:
            return {"status": 403, "error": "jwt verification failed"}
        # Defense-in-depth claim check: decoded session_id MUST equal env.SESSION_ID.
        if str(claims.get("session_id")) != str(self._session_id):
            return {"status": 403, "error": "session_id claim mismatch"}
        self.executed.append({"tool": tool, "args": dict(args or {}), "session_id": self._session_id})
        return {"status": 200, "tool": tool, "executed_in": self._session_id}
