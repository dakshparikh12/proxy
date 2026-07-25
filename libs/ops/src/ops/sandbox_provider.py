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

# Alive-state registry: sandbox id -> alive?. A destroyed sandbox reads back
# not-alive; an unknown id defaults to alive (the historical health_check True).
_ALIVE: dict[str, bool] = {}

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
    returned handle is awaitable for the async boundary. Idempotent per
    ``meeting_id`` (§3.9): the sandbox id is deterministic (``sbx-<meeting_id>``),
    so a repeat provision for a still-live meeting returns the EXISTING handle and
    never a second sandbox — the load-bearing assumption that keeps the whole
    lifecycle broker-free.
    """
    existing = _LIVE_BY_MEETING.get(meeting_id)
    if existing is not None and _ALIVE.get(existing.id, False):
        return existing  # a live sandbox already exists → return it (no re-provision)
    backstop = int(timeout_s) if timeout_s is not None else sandbox_timeout_s()
    sandbox_id = f"sbx-{meeting_id}"
    _ALIVE[sandbox_id] = True
    handle = SandboxHandle(id=sandbox_id, meeting_id=meeting_id, timeout_s=backstop)
    _LIVE_BY_MEETING[meeting_id] = handle
    _PROVISIONED_AT[meeting_id] = time.monotonic()
    _ENDED_MEETINGS.discard(meeting_id)  # a fresh provision un-ends a re-joined meeting
    return handle


def destroy(handle: SandboxHandle | Any) -> _AsyncNone:
    """Idempotent: tear the sandbox down (a no-op if already gone; tolerates 404)."""
    _ALIVE[_key(handle)] = False
    meeting_id = getattr(handle, "meeting_id", None)
    if meeting_id is not None:
        _LIVE_BY_MEETING.pop(meeting_id, None)
        _PROVISIONED_AT.pop(meeting_id, None)
    return _AsyncNone()


def health_check(handle: SandboxHandle | Any) -> SandboxHealth:
    """Idempotent: report whether the sandbox is reachable."""
    return SandboxHealth(alive=_ALIVE.get(_key(handle), True))


async def pre_provision(*, join_event: dict[str, Any]) -> SandboxHandle:
    """Pre-provision on a creation/join event (never from a warm idle pool)."""
    meeting_id = str(join_event.get("meeting_id", ""))
    return await provision(meeting_id=meeting_id)


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
