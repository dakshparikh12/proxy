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

import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from libs.db import sandbox_timeout_s, sandbox_ttl_s

from . import sandbox as _sandbox

# Alive-state registry: sandbox id -> alive?. A destroyed sandbox reads back
# not-alive; an unknown id defaults to alive (the historical health_check True).
_ALIVE: dict[str, bool] = {}

# The host-kept sandbox → per-sandbox JWT secret map (§3.5 / CANONICAL §12.9).
# Each live sandbox has its OWN random HS256 secret; the host (never the sandbox)
# owns this map. There is deliberately NO fleet-shared secret constant: untrusted
# in-sandbox repo code that exfiltrated a shared secret could forge a token
# accepted by ANOTHER sandbox, so the map is per-sandbox-id and the secret dies
# with the sandbox on destroy.
_SECRET_BY_SANDBOX: dict[str, str] = {}

# The live-sandbox view the TTL reconcile cron reconciles against — there is NO
# sandbox registry TABLE (no warm pool, no FSM per §6); this is the in-process
# analogue of the E2B API's live-sandbox list. A meeting maps to its ONE live
# handle so ``ensure_running`` returns the existing sandbox on a redelivered join
# and ``list_sandboxes`` yields the orphan candidates for the cron. In prod this
# view is the E2B ``list`` call; in the local substrate build it is this dict.
_LIVE_BY_MEETING: dict[str, "SandboxHandle"] = {}
# meeting_id -> monotonic provision time (age source for the TTL cron).
_PROVISIONED_AT: dict[str, float] = {}
# meetings whose close ran — the cron cross-checks live sandboxes against these.
_ENDED_MEETINGS: set[str] = set()

# The operation_runs operation_type the race-safe ensure_running claims on — one
# 'running' claim row per meeting gates the single provision (§3.9 race-safe call).
SANDBOX_PROVISION_OP = "sandbox-provision"


def _key(handle_or_id: Any) -> str:
    """Extract the sandbox id from a handle or accept a raw id string."""
    return str(getattr(handle_or_id, "id", handle_or_id))


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to a live E2B sandbox, its per-sandbox JWT secret, and
    its timeout backstop.

    Awaitable so ``await provision(...)`` yields the handle itself; also usable
    directly (``provision(...).id``) on the synchronous path. ``jwt_secret`` is the
    DISTINCT random HS256 secret minted for THIS sandbox at provision (§3.5); the
    host keeps the authoritative sandbox→secret map (``secret_for``), the handle
    carries a copy for the token_provider that signs this sandbox's JWTs.
    """

    id: str
    meeting_id: str
    timeout_s: int
    jwt_secret: str = ""
    tenant: str = ""

    @property
    def sandbox_id(self) -> str:  # back-compat alias for the historical field
        return self.id

    @property
    def session_id(self) -> str:
        """The per-sandbox claim id (§3.5) — the decoded JWT ``session_id`` must
        equal ``env.SESSION_ID`` in-sandbox; the sandbox id is that unique claim."""
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
    """An awaitable that is a no-op when awaited (destroy's async return).

    When ``coro`` is supplied (a real backend kill), awaiting runs it; the sync
    path (``destroy(sb.id)`` never awaited) simply drops it — the host-side
    bookkeeping already ran synchronously in ``destroy`` before this was returned.
    """

    def __init__(self, coro: Any = None) -> None:
        self._coro = coro

    def __await__(self) -> Generator[Any, None, None]:
        async def _run() -> None:
            if self._coro is not None:
                await self._coro
            return None

        return _run().__await__()

    def __del__(self) -> None:  # never warn on a dropped, never-awaited backend coro
        coro = getattr(self, "_coro", None)
        if coro is not None:
            close = getattr(coro, "close", None)
            if close is not None:
                close()


def verbs() -> set[str]:
    """The three idempotent provider verbs — no FSM, no warm pool."""
    return {"provision", "destroy", "health_check"}


def provision(
    *, meeting_id: str, tenant: str = "", timeout_s: int | None = None
) -> SandboxHandle:
    """Idempotent: create the sandbox with an E2B timeout backstop AND its own
    distinct random per-sandbox JWT secret (§3.5 / CANONICAL §12.9).

    ``timeout_s`` is the E2B-native auto-kill backstop — the sandbox self-expires
    even if every other bound (explicit destroy, TTL reconcile) is missed. The
    returned handle is awaitable for the async boundary. Idempotent per
    ``meeting_id`` (§3.9): the sandbox id is deterministic (``sbx-<meeting_id>``),
    so a repeat provision for a still-live meeting returns the EXISTING handle —
    with its ALREADY-MINTED secret unchanged — and never a second sandbox nor a
    fresh secret (the load-bearing assumption that keeps the whole lifecycle
    broker-free). A cold (or re-provisioned-after-destroy) meeting mints a FRESH
    distinct secret via ``ops.sandbox.provision_sandbox`` so no two sandboxes ever
    share one.
    """
    existing = _LIVE_BY_MEETING.get(meeting_id)
    if existing is not None and _ALIVE.get(existing.id, False):
        return existing  # a live sandbox already exists → return it (no re-provision)
    backstop = int(timeout_s) if timeout_s is not None else sandbox_timeout_s()
    sandbox_id = f"sbx-{meeting_id}"
    # Mint the DISTINCT random per-sandbox HS256 secret (the §3.5 primitive). A new
    # secret on every cold provision → two provisions never share a secret.
    minted = _sandbox.provision_sandbox(tenant=tenant, meeting_id=meeting_id)
    jwt_secret = minted.jwt_secret
    _SECRET_BY_SANDBOX[sandbox_id] = jwt_secret  # host keeps the sandbox→secret map
    _ALIVE[sandbox_id] = True
    handle = SandboxHandle(
        id=sandbox_id,
        meeting_id=meeting_id,
        timeout_s=backstop,
        jwt_secret=jwt_secret,
        tenant=tenant,
    )
    _LIVE_BY_MEETING[meeting_id] = handle
    _PROVISIONED_AT[meeting_id] = time.monotonic()
    _ENDED_MEETINGS.discard(meeting_id)  # a fresh provision un-ends a re-joined meeting
    return handle


def secret_for(sandbox_id_or_handle: Any) -> str | None:
    """The host-side lookup into the sandbox→secret map (§3.5).

    Returns this sandbox's per-sandbox JWT secret, or ``None`` if the host holds no
    secret for it (a destroyed / never-provisioned sandbox). The token_provider
    signs a sandbox's JWTs with THIS secret; a token minted for one sandbox can
    never verify against another's secret.
    """
    return _SECRET_BY_SANDBOX.get(_key(sandbox_id_or_handle))


def destroy(handle: SandboxHandle | Any, *, backend: Any = None) -> _AsyncNone:
    """Idempotent: tear the sandbox down (a no-op if already gone; tolerates 404).

    The per-sandbox secret is dropped from the host map on destroy so it can never
    be reused. When a real E2B ``backend`` is injected, the kill is issued through
    the ``call_external`` seam and tolerates an already-killed sandbox (404 → ok).
    """
    sandbox_id = _key(handle)
    _ALIVE[sandbox_id] = False
    _SECRET_BY_SANDBOX.pop(sandbox_id, None)  # the secret dies with the sandbox
    meeting_id = getattr(handle, "meeting_id", None)
    if meeting_id is not None:
        _LIVE_BY_MEETING.pop(meeting_id, None)
        _PROVISIONED_AT.pop(meeting_id, None)
    if backend is not None:
        return _AsyncNone(coro=_backend_kill(backend, sandbox_id))
    return _AsyncNone()


def health_check(handle: SandboxHandle | Any) -> SandboxHealth:
    """Idempotent: report whether the sandbox is reachable."""
    return SandboxHealth(alive=_ALIVE.get(_key(handle), True))


async def pre_provision(*, join_event: dict[str, Any]) -> SandboxHandle:
    """Pre-provision on a creation/join event (never from a warm idle pool).

    This is the §3.9 meeting-creation trigger: exactly ONE sandbox for the meeting
    the event names, spun WHEN the meeting is created/joined — not held in a
    standing keepalive pool and never cold-booted mid-meeting.
    """
    meeting_id = str(join_event.get("meeting_id", ""))
    tenant = str(join_event.get("tenant", ""))
    return await provision(meeting_id=meeting_id, tenant=tenant)


# ── The real E2B backend seam (lazy, behind call_external, honest-degrade) ────
#
# The E2B wire surface confirmed against LIVE E2B docs (CANONICAL §11.10):
#   AsyncSandbox.create(template=, timeout=<seconds>, envs=<dict>, metadata=<dict>)
#   instance .kill() / .set_timeout(<seconds>) / .is_running()
#   classmethods AsyncSandbox.connect(sandbox_id) / AsyncSandbox.list()
# `envs` is where the per-sandbox JWT_SECRET + SESSION_ID (the claim id) go (§3.5).
# The real AsyncSandbox is constructed ONLY in libs/http (the call_external home);
# THIS module never imports e2b. The RESIDUAL that cannot be produced in-session —
# the E2B template BAKE (the Node workspace-mcp-server sidecar + ast-grep baked
# into the template image) and LIVE sandbox execution — is a DEPLOY artifact
# (Phase-3 / founder infra), flagged, not faked.

E2B_TEMPLATE = "proxy-workroom"  # the baked template id (bake = deploy residual)


class _RealE2BBackend:
    """The live E2B backend — issues every op through the ``call_external`` seam.

    Constructed lazily and only when a live provision runs; imports of the E2B SDK
    happen inside ``libs/http.e2b_sandbox_class`` (the sole raw-client home), never
    here. Absent the ``e2b`` package this raises ``ImportError`` at first use — the
    caller degrades honestly to the in-process substrate view.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Any] = {}  # sandbox_id -> live AsyncSandbox instance

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
        from libs.http.src.http import external as _http

        cls = _http.e2b_sandbox_class()  # ImportError here iff e2b absent (honest)
        # The egress ``network=`` kwarg (default-DENY + curated allow-list, §3.10) rides the
        # real create so a non-allowlisted host is unreachable — it is NOT discarded (which
        # would inherit E2B's default-ALLOW outbound). The EXACT live E2B network wire SHAPE
        # (``network={denyOut,allowOut}`` vs. an alternate) is a Phase-3 real-infra item to
        # confirm against LIVE E2B docs when the sandbox is stood up (CANONICAL §11.10 — wire
        # shapes are NOT doc-pinned as verified); the host-side threading is what lands here.
        create_kwargs: dict[str, Any] = {
            "template": template,
            "timeout": timeout,
            "envs": envs,
            "metadata": metadata,
        }
        if network is not None:
            create_kwargs["network"] = network
        outcome = await _http.call_external(
            lambda: cls.create(**create_kwargs),
            service="e2b",
        )
        instance = outcome.value
        self._by_id[sandbox_id] = instance
        return str(getattr(instance, "sandbox_id", sandbox_id))

    async def kill(self, *, sandbox_id: str) -> None:
        from libs.http.src.http import external as _http

        instance = self._by_id.pop(sandbox_id, None)
        if instance is None:
            return  # already gone (404 → idempotent no-op)
        await _http.call_external(lambda: instance.kill(), service="e2b")

    async def set_timeout(self, *, sandbox_id: str, timeout: int) -> None:
        from libs.http.src.http import external as _http

        instance = self._by_id.get(sandbox_id)
        if instance is None:
            return
        await _http.call_external(lambda: instance.set_timeout(timeout), service="e2b")

    async def is_running(self, *, sandbox_id: str) -> bool:
        from libs.http.src.http import external as _http

        instance = self._by_id.get(sandbox_id)
        if instance is None:
            return False
        outcome = await _http.call_external(lambda: instance.is_running(), service="e2b")
        return bool(outcome.value)


async def heartbeat_bump(handle: SandboxHandle, *, backend: Any = None, timeout_s: int | None = None) -> None:
    """The §3.9 heartbeat activity-bump: extend the sandbox timeout so a
    silently-thinking build's sandbox is NOT reaped by the E2B timeout backstop.

    E2B's ``set_timeout(seconds)`` resets the auto-kill countdown to ``seconds``
    from now; a bump must therefore push the deadline STRICTLY BEYOND the
    provision-time backstop, or a build that stays silent longer than the original
    ``handle.timeout_s`` window would still be reaped — the exact §3.9 failure this
    bump exists to prevent. So the default extension is a FRESH full activity window
    ADDED ON TOP of the base backstop (``handle.timeout_s + one base window``): each
    bump provably moves the auto-kill deadline forward past the original backstop.

    Idempotent + safe to call on a bump cadence; a no-op when the sandbox is gone.
    Runs through the backend's ``set_timeout`` (behind ``call_external``).
    """
    if not _ALIVE.get(handle.id, False):
        return
    if timeout_s is not None:
        extend_to = int(timeout_s)
    else:
        # A fresh activity window on top of the provision-time backstop — strictly
        # greater than ``handle.timeout_s`` so the extension is real (not a no-op
        # re-set of the same deadline) and the anti-reap is load-bearing.
        extend_to = handle.timeout_s + sandbox_timeout_s()
    if backend is not None:
        await backend.set_timeout(sandbox_id=handle.id, timeout=extend_to)


async def _backend_kill(backend: Any, sandbox_id: str) -> None:
    """Await the backend kill (idempotent; tolerates an already-gone sandbox)."""
    await backend.kill(sandbox_id=sandbox_id)


def _reset_for_test() -> None:
    """Clear ALL in-process provider state (test isolation — no cross-test warm state)."""
    _ALIVE.clear()
    _SECRET_BY_SANDBOX.clear()
    _LIVE_BY_MEETING.clear()
    _PROVISIONED_AT.clear()
    _ENDED_MEETINGS.clear()


async def ensure_running(
    db: Any,
    meeting_id: str,
    *,
    provision: Any = None,  # noqa: A002 - the caller's provision factory (shadows the verb by design)
) -> SandboxHandle:
    """The ONE race-safe 'get me a healthy sandbox for this meeting NOW' call (§3.9).

    Idempotent per meeting. A cold meeting is provisioned EXACTLY ONCE; a
    redelivered join (or a concurrent duplicate join) returns the EXISTING sandbox
    with NO second provision. Race-safety is the ``operation_runs`` atomic claim
    (the same Postgres arbiter the meeting-harness claim uses): the single
    'running' ``sandbox-provision`` row per meeting means exactly one caller wins
    the claim and provisions; a loser finds the winner's live handle and returns it.

    ``provision`` is the caller's factory (``meeting_id -> handle``); it defaults to
    the module ``provision`` verb. Health that reads ``gone``/not-alive re-provisions
    a fresh sandbox (never a doomed restart) — also idempotent per §3.9.
    """
    factory = provision if provision is not None else _default_provision_factory

    # Fast path: a healthy live sandbox already exists → return it (no claim needed).
    existing = _LIVE_BY_MEETING.get(meeting_id)
    if existing is not None and bool(health_check(existing)):
        return existing

    # Contended/cold path: race for the atomic claim so exactly ONE join provisions.
    won = await _claim_provision(db, meeting_id)
    if not won:
        # A concurrent join won the claim; wait briefly for its live handle to land,
        # then return it — never a second provision (§3.9 "can't double-provision").
        landed = await _await_live_handle(meeting_id)
        if landed is not None:
            return landed
        # The winner's handle never landed (it errored); fall through and provision.

    result = factory(meeting_id)
    handle: SandboxHandle = await result if _isawaitable(result) else result
    return handle


def _default_provision_factory(meeting_id: str) -> SandboxHandle:
    """The default ensure_running factory — the idempotent module ``provision`` verb."""
    return provision(meeting_id=meeting_id)


def _isawaitable(obj: Any) -> bool:
    import inspect

    return inspect.isawaitable(obj)


async def _claim_provision(db: Any, meeting_id: str) -> bool:
    """Win the atomic operation_runs claim for this meeting's single provision.

    Returns True iff THIS caller won the 'running' ``sandbox-provision`` row (the
    partial unique index admits exactly one). A ``db`` without a claim capability
    (a test double) defaults to won=True so the single-caller path still provisions.
    """
    from .claim import claim_meeting

    acquire = getattr(db, "acquire", None)
    if acquire is None:
        return True
    run_id = await claim_meeting(db, meeting_id, SANDBOX_PROVISION_OP)
    return run_id is not None


async def _await_live_handle(
    meeting_id: str, *, attempts: int = 50, delay_s: float = 0.01
) -> SandboxHandle | None:
    """Poll briefly for the winning join's live handle (bounded, never blocks forever)."""
    import asyncio

    for _ in range(attempts):
        handle = _LIVE_BY_MEETING.get(meeting_id)
        if handle is not None and bool(health_check(handle)):
            return handle
        await asyncio.sleep(delay_s)
    return None


def mark_meeting_ended(meeting_id: str) -> None:
    """Record that a meeting's ordered close ran — the cron reaps its live sandbox.

    The ordered close (§3.16) calls this so the TTL/orphan reconcile cross-checks
    live sandboxes against ended meetings (§3.9 defence #3) and destroys any that
    survived the explicit close (the reconcile is the cost backstop, not correctness).
    """
    _ENDED_MEETINGS.add(str(meeting_id))


def list_sandboxes() -> list[SandboxHandle]:
    """List every live sandbox — the cron's orphan-candidate view (§3.9 reconcile).

    In prod this is the E2B ``list`` API call; in the local substrate build it is
    the in-process live-sandbox view. Only currently-alive handles are yielded.
    """
    return [h for h in _LIVE_BY_MEETING.values() if _ALIVE.get(h.id, False)]


def _is_orphan_or_past_ttl(handle: SandboxHandle, *, ttl_s: int | None = None) -> bool:
    """A live sandbox is reapable iff its meeting ended OR its age exceeds the TTL."""
    if handle.meeting_id in _ENDED_MEETINGS:
        return True
    ttl = int(ttl_s) if ttl_s is not None else sandbox_ttl_s()
    started = _PROVISIONED_AT.get(handle.meeting_id)
    if started is None:
        return False
    return (time.monotonic() - started) > ttl


async def reconcile_sandboxes(ttl_s: int | None = None) -> int:
    """The §3.8 cron step: list live sandboxes, destroy any orphaned or past-TTL.

    Idempotent — a second run over the reaped state finds nothing to destroy.
    Returns the count of sandboxes reaped. No per-row status transitions, no FSM
    (cut per §6): just "list, find the orphans, destroy them."
    """
    reaped = 0
    for handle in list_sandboxes():
        if _is_orphan_or_past_ttl(handle, ttl_s=ttl_s):
            await destroy(handle)  # tolerates 404 → 'gone'
            reaped += 1
    return reaped
