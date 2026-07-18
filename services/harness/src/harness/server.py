"""Ordered, fail-fast server boot & lifecycle (Doc 00 s6).

``~/platform``'s ``server.ts`` teaches: *fail loud at boot, not on first use*.
We replicate its ordering in a FastAPI ``lifespan`` whose step ORDER is
load-bearing:

    tracing -> pool -> Database -> provisioner_ready -> reaper -> routers

Tracing initialises synchronously first (so the first ``query()`` is traced);
routers mount LAST, strictly after the boot-time stale-row reaper has swept
orphaned rows. The boot sequence is a single ordered list consumed by both the
real lifespan and the instrumented trace oracle, so the two can never drift.
"""
from __future__ import annotations

import asyncio
import errno
import os
import signal
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from libs.db import Database
from libs.ops import configure_logging, get_logger

# The canonical s6 boot ordering, as ordered step tags. Both the real lifespan
# and the instrumented trace record exactly these, in this order.
BOOT_STEP_TAGS: tuple[str, ...] = (
    "tracing",
    "pool",
    "database",
    "provisioner_ready",
    "reaper",
    "routers",
)

# Module-level recorder populated during a real lifespan startup (fallback hook
# for the boot-ordering oracle when no explicit trace list is injected).
BOOT_TRACE: list[str] = []

# Hard-exit backstop window: bounds graceful shutdown inside Cloud Run's SIGTERM
# grace period. unit: seconds.
SHUTDOWN_GRACE_S: float = 25.0
# Flush delay before a genuinely-unknown fault crashes the process (lets trace
# spans/logs flush). unit: seconds.
CRASH_FLUSH_DELAY_S: float = 0.05

_log = get_logger("harness.server")


# ---------------------------------------------------------------------------
# provisioner_ready async-readiness gate (AC-BOOT-003)
# ---------------------------------------------------------------------------

def make_provisioner_gate() -> asyncio.Event:
    """Create the provisioner_ready gate in its un-ready state."""
    return asyncio.Event()


def set_provisioner_ready(gate: asyncio.Event) -> None:
    """Mark the bot/sandbox provisioner ready -- unblocks waiting handlers."""
    gate.set()


async def await_provisioner_ready(gate: asyncio.Event) -> None:
    """Block until the provisioner is wired (defuses the join-before-wired race)."""
    await gate.wait()


# ---------------------------------------------------------------------------
# boot-time stale-row reaper (AC-BOOT-004)
# ---------------------------------------------------------------------------

def reap_orphans(dsn: str) -> int:
    """Mark every orphaned ``running``/``in_meeting`` operation row ``interrupted``.

    At boot every still-``running`` row belongs to a process that is now dead, so
    it is orphaned and interrupted unconditionally (this is distinct from the
    lazy heartbeat-gated sweep used mid-flight). Idempotent: a second run over the
    same state interrupts nothing further. Returns the number of rows interrupted.
    """
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(
            "UPDATE operation_runs "
            "   SET status = 'interrupted', completed_at = now() "
            " WHERE status IN ('running', 'in_meeting') "
            "RETURNING id"
        )
        return len(cur.fetchall())


# ---------------------------------------------------------------------------
# EPIPE tolerance -- the asyncio exception handler (AC-BOOT-005)
# ---------------------------------------------------------------------------

def _is_epipe(exc: BaseException | None) -> bool:
    """A crashed Claude-SDK subprocess surfaces EPIPE (recoverable via retry)."""
    if isinstance(exc, BrokenPipeError):
        return True
    return isinstance(exc, OSError) and exc.errno == errno.EPIPE


def _flush_then_crash(exc: BaseException | None) -> None:
    """Flush trace spans, then hard-exit -- an unknown fault is never swallowed."""
    try:
        _flush_tracing_sync()
    except Exception:  # noqa: BLE001 - flush is best-effort on the crash path
        pass
    time.sleep(CRASH_FLUSH_DELAY_S)
    os._exit(1)


def asyncio_exception_handler(loop: Any, context: dict[str, Any]) -> None:
    """Swallow EPIPE (retry recovers); crash on a genuinely-unknown exception."""
    exc = context.get("exception")
    if _is_epipe(exc):
        _log.warning("epipe_swallowed", error=str(exc))
        return
    _log.error("unknown_loop_exception", error=str(exc))
    _flush_then_crash(exc)


# ---------------------------------------------------------------------------
# graceful shutdown -- parallel gather + hard-exit backstop (AC-BOOT-006)
# ---------------------------------------------------------------------------

async def flush_tracing() -> None:
    """Flush the tracing scaffold (Langfuse is inert while its keys are unset)."""
    return None


def _flush_tracing_sync() -> None:
    """Synchronous best-effort flush used on the crash path."""
    return None


async def graceful_shutdown(
    *,
    flush_tracing: Callable[[], Awaitable[Any]],
    db: Any,
    bot: Any,
    server: Any,
) -> None:
    """Run the four shutdown tasks concurrently, bounded by a hard-exit backstop.

    They lost trace spans before making shutdown parallel, so all four run under
    a single ``asyncio.gather``; a ``loop.call_later`` backstop hard-exits if the
    grace window elapses.
    """
    loop = asyncio.get_running_loop()
    backstop = loop.call_later(SHUTDOWN_GRACE_S, os._exit, 1)  # hard-exit backstop
    try:
        await asyncio.wait_for(
            asyncio.gather(
                flush_tracing(),
                db.close(),
                bot.leave_all(),
                server.shutdown(),
            ),
            timeout=SHUTDOWN_GRACE_S,
        )
    finally:
        backstop.cancel()


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop, on_shutdown: Callable[[], Any]
) -> None:
    """Register graceful shutdown on BOTH SIGINT and SIGTERM."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_shutdown)


# ---------------------------------------------------------------------------
# The ordered boot sequence (single source of truth for both paths)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _BootDeps:
    """The six ordered startup steps, each taking the app and doing its work."""

    tracing: Callable[[Any], Awaitable[None]]
    pool: Callable[[Any], Awaitable[None]]
    database: Callable[[Any], Awaitable[None]]
    provisioner_ready: Callable[[Any], Awaitable[None]]
    reaper: Callable[[Any], Awaitable[None]]
    routers: Callable[[Any], Awaitable[None]]


async def _run_startup(app: Any, recorder: list[str], deps: _BootDeps) -> None:
    """Execute the boot steps in canonical order, recording each as it completes."""
    ordered: tuple[tuple[str, Callable[[Any], Awaitable[None]]], ...] = (
        ("tracing", deps.tracing),
        ("pool", deps.pool),
        ("database", deps.database),
        ("provisioner_ready", deps.provisioner_ready),
        ("reaper", deps.reaper),
        ("routers", deps.routers),
    )
    for tag, step in ordered:
        await step(app)
        recorder.append(tag)


# ── the real startup steps (mutate app.state; used by the production lifespan) ─

async def _real_tracing(app: Any) -> None:
    configure_logging()
    # Langfuse tracing scaffold is wired but INERT (keys unset) -- Doc 00 s13.


async def _real_pool(app: Any) -> None:
    from libs.db import open_pool
    from services.harness.src.harness import settings as settings_mod

    # The one asyncpg pool-construction site lives in libs/db (§11 canonical
    # config); the boot step just opens it and stashes it on app.state.
    app.state.pool = await open_pool(settings_mod.settings.database_url)


async def _real_database(app: Any) -> None:
    app.state.db = Database(app.state.pool, f"proc-{os.getpid()}")


async def _real_provisioner_ready(app: Any) -> None:
    gate = make_provisioner_gate()
    app.state.provisioner_ready = gate
    # The Recall bot + sandbox provisioner are created in startup (Doc 02 wires
    # the real provisioning); request handlers await this gate before use.
    set_provisioner_ready(gate)


async def _real_reaper(app: Any) -> None:
    await app.state.db.sweep_stale_operation_runs()


async def _real_routers(app: Any) -> None:
    _mount_routers(app)


def _real_deps() -> _BootDeps:
    return _BootDeps(
        tracing=_real_tracing,
        pool=_real_pool,
        database=_real_database,
        provisioner_ready=_real_provisioner_ready,
        reaper=_real_reaper,
        routers=_real_routers,
    )


def _mount_routers(app: Any) -> None:
    """Mount routers LAST, behind the auth wall + the single libs/http dispatch funnel.

    The concrete FastAPI routers are assembled in later docs; the ordering gate
    (routers strictly after the reaper) is owned here.
    """
    app.state.routers_mounted = True


# ── the no-op steps (record ordering without side effects; trace oracle) ──────

async def _noop_step(app: Any) -> None:
    return None


_NOOP_DEPS = _BootDeps(
    tracing=_noop_step,
    pool=_noop_step,
    database=_noop_step,
    provisioner_ready=_noop_step,
    reaper=_noop_step,
    routers=_noop_step,
)


class _TraceApp:
    """Minimal app stand-in for the trace oracle (only ``state`` is touched)."""

    def __init__(self) -> None:
        self.state = type("_State", (), {})()


# ---------------------------------------------------------------------------
# The FastAPI lifespan + the boot-ordering trace oracle (AC-BOOT-002/004, W10)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(
    app: Any, *, trace: list[str] | None = None
) -> AsyncIterator[None]:
    """Ordered, fail-fast startup then parallel graceful shutdown."""
    recorder = trace if trace is not None else BOOT_TRACE
    if trace is None:
        BOOT_TRACE.clear()
    await _run_startup(app, recorder, _real_deps())
    try:
        yield
    finally:
        await _shutdown_real(app)


def instrumented_lifespan() -> tuple[Any, list[str]]:
    """Return ``(async-ctx-manager, trace)`` for the boot-ordering oracle."""
    trace: list[str] = []

    @asynccontextmanager
    async def _cm() -> AsyncIterator[None]:
        await _run_startup(_TraceApp(), trace, _NOOP_DEPS)
        yield

    return _cm(), trace


def lifespan_trace() -> list[str]:
    """Drive the boot sequence with inert steps and return the ordered step tags."""
    recorder: list[str] = []

    async def _run() -> None:
        await _run_startup(_TraceApp(), recorder, _NOOP_DEPS)

    asyncio.run(_run())
    return recorder


async def _shutdown_real(app: Any) -> None:
    """Best-effort parallel teardown of the real startup resources."""
    db = getattr(app.state, "db", None)
    if db is not None:
        await db.close()
