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


class FakeSandboxFilesystem:
    """An in-process stand-in for the E2B sandbox filesystem read-back path.

    Mirrors the confirmed live wire (CANONICAL §11.10): ``sandbox.files.read(path,
    format="bytes") -> bytearray`` and ``files.write(path, data)``. It is the ONLY
    channel the host trusts for a file's LANDED bytes: the host-side receipt capture
    reads a touched file back through :meth:`read_bytes` and hashes THOSE bytes
    itself, so a tool that CLAIMS a different hash is structurally ignored. A file the
    tool claims it touched but never actually wrote is simply absent → ``read_bytes``
    returns ``None`` (the host records an empty hash, never crashes).
    """

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def write(self, path: str, data: bytes) -> None:
        """Land ``data`` at ``path`` inside the (fake) sandbox — the real write effect."""
        self._files[path] = bytes(data)

    async def read_bytes(self, path: str) -> bytes | None:
        """Read a landed file back as raw bytes (E2B ``files.read(path, format='bytes')``).

        Returns ``None`` when the file never landed — so the host's capture records an
        empty hash for a claimed-but-absent artifact rather than raising (Rule 6)."""
        data = self._files.get(path)
        return None if data is None else bytes(data)


class FakeToolSidecar:
    """An in-process stand-in for the E2B-baked sidecar's ``tools/result`` return.

    In production a tool runs INSIDE the sandbox and its ``tools/result`` returns to the
    host over HTTP; this fake produces the SAME structured result payload the host-side
    receipt capture consumes. The load-bearing feature is ``lying_claim``: a
    model/sidecar-supplied narration (``text``, or a claimed ``exit_code`` / claimed
    ``artifact_hashes``) planted ALONGSIDE the real captured stream, so a test can prove
    the host capture reads only the REAL stream fields (the kernel exit status + the
    landed bytes) and NEVER the lying claim (§3.5 / §3.7② / CANONICAL §12.4).

    The structured result shape (what the host trusts): ``captured`` — the real captured
    stream the sidecar observed on the host boundary (``argv`` / ``exit_code`` / ``stdout``
    for run_command; ``touched`` paths for a write). ``claim`` — the untrusted
    model-facing narration the host must IGNORE.
    """

    def __init__(self, *, fs: FakeSandboxFilesystem) -> None:
        self._fs = fs

    async def run_command(
        self,
        *,
        argv: list[str],
        exit_code: int,
        stdout: bytes,
        lying_claim: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the structured ``tools/result`` for a run_command — the REAL captured
        ``argv`` / ``exit_code`` / ``stdout`` bytes, plus an optional lying ``claim``."""
        return {
            "tool": "run_command",
            "captured": {"argv": list(argv), "exit_code": int(exit_code), "stdout": bytes(stdout)},
            "claim": dict(lying_claim or {}),
        }

    async def write_file(
        self,
        *,
        path: str,
        content: bytes,
        tool: str = "write_file",
        landed: bool = True,
        lying_claim: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the structured ``tools/result`` for a write/edit/ast_grep.

        When ``landed`` (the default), the bytes are ACTUALLY written into the fake
        sandbox fs so the host can read them back and hash them; ``landed=False`` models a
        tool that CLAIMS a file was touched but never wrote it (the host then records an
        empty hash for that path, never crashes). ``lying_claim`` plants an untrusted
        claimed ``artifact_hashes`` the host must ignore."""
        if landed:
            self._fs.write(path, content)
        return {
            "tool": tool,
            "captured": {"touched": [path], "exit_code": 0},
            "claim": dict(lying_claim or {}),
        }

    async def read_file(self, *, path: str, content: bytes) -> dict[str, Any]:
        """Return the structured ``tools/result`` for a read-only tool — touches nothing,
        so the host produces NO effect-receipt."""
        return {"tool": "read_file", "captured": {"content": bytes(content)}, "claim": {}}


class FakeE2BBackend:
    """A fake E2B backend with the shape ``sandbox_provider`` drives.

    Mirrors the injected-seam contract: ``create`` / ``kill`` / ``set_timeout`` /
    ``is_running``, each issued through this fake's own ``call_external`` so the
    test can assert the host never touches a raw client.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.create_envs: dict[str, dict[str, str]] = {}
        # The egress network= kwarg the sandbox was actually created with (§3.10). The
        # real ``AsyncSandbox.create`` wire arg lives behind ``call_external`` in libs/http
        # and stays a Phase-3 residual; this fake proves the host THREADED the computed
        # default-DENY policy into the create call (a non-allowlisted host is unreachable),
        # rather than discarding it and inheriting E2B's default-ALLOW outbound.
        self.create_network: dict[str, dict[str, Any] | None] = {}
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
        self,
        *,
        sandbox_id: str,
        template: str,
        timeout: int,
        envs: dict[str, str],
        metadata: dict[str, str],
        network: dict[str, Any] | None = None,
    ) -> str:
        # Route the create through call_external, exactly as the real backend does.
        # ``network`` is the confirmed-live E2B egress kwarg the host threads in (§3.10);
        # the fake records it so a test can prove default-DENY + the curated allow-list
        # actually rode the create call (not the discarded default-ALLOW).
        async def _raw() -> str:
            self.created.append(sandbox_id)
            self.create_envs[sandbox_id] = dict(envs)
            self.create_network[sandbox_id] = dict(network) if network is not None else None
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
