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
# Routine MIG-drain grace window: how long a draining meeting_runtime instance
# waits for its in-flight meetings to finish before it exits. The GCE MIG gives
# real grace (minutes, not Cloud Run's 10s SIGTERM — §6 lines 37/97), so this is
# generous. Env override ``DRAIN_GRACE_S`` for ops tuning. unit: seconds.
DRAIN_GRACE_S: float = float(os.environ.get("DRAIN_GRACE_S", "300"))
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
# Routine MIG-drain: the ``draining`` state + /readiness probe + finish-in-flight
#
# Distinct from the §5 heartbeat/reclaim exception (defense-in-depth for a rare
# HARD drain): this is the ROUTINE path a GCE MIG uses when it recycles a
# meeting_runtime/code_intel instance. On SIGTERM we set ``draining`` (so the MIG
# stops routing new meetings via the 503 /readiness probe), let the in-flight
# meetings finish within a real grace window, then exit cleanly.
# ---------------------------------------------------------------------------

def init_drain_state(app: Any) -> None:
    """Initialise the routine-drain state on ``app.state`` (ready, no in-flight)."""
    app.state.draining = False
    if getattr(app.state, "meeting_tasks", None) is None:
        app.state.meeting_tasks = set()


def is_draining(app: Any) -> bool:
    """True once the instance has begun a routine MIG drain."""
    return bool(getattr(app.state, "draining", False))


def set_draining(app: Any, value: bool = True) -> None:
    """Set the ``draining`` flag (the /readiness probe reads it to answer 503/200)."""
    app.state.draining = bool(value)


def register_inflight(app: Any, task: Any) -> None:
    """Track an in-flight meeting task so a routine drain can wait for it to finish."""
    if getattr(app.state, "meeting_tasks", None) is None:
        app.state.meeting_tasks = set()
    app.state.meeting_tasks.add(task)
    # A finished task drops itself so the in-flight set never leaks completed meetings.
    task.add_done_callback(lambda t: app.state.meeting_tasks.discard(t))


def _inflight_tasks(app: Any) -> list[Any]:
    """The still-running in-flight meeting tasks (completed ones are pruned)."""
    tasks = getattr(app.state, "meeting_tasks", None) or set()
    return [t for t in tasks if not t.done()]


async def begin_drain(
    app: Any,
    *,
    grace_s: float | None = None,
    exit_fn: Callable[..., Any] = os._exit,
) -> None:
    """Run the ROUTINE MIG-drain: set ``draining`` → let in-flight meetings finish → exit.

    1. Flip ``draining`` so every subsequent /readiness probe answers 503 and the MIG
       stops routing new meetings to this instance.
    2. Wait — bounded by ``grace_s`` — for the in-flight meeting tasks to finish
       naturally (a live meeting is allowed to wrap up; it is NOT cut off mid-flight —
       that hard-reclaim story is the §5 heartbeat exception, not this routine path).
    3. Exit cleanly via ``exit_fn`` (default ``os._exit(0)``). The grace bound means an
       overrunning meeting can never wedge the drain open forever.
    """
    set_draining(app, True)
    grace = DRAIN_GRACE_S if grace_s is None else grace_s
    pending = _inflight_tasks(app)
    if pending:
        # Wait for the in-flight meetings, bounded by the grace window. Never
        # cancel them here — a routine drain lets a live meeting finish; the
        # grace bound + the exit backstop cap the wait.
        try:
            await asyncio.wait(pending, timeout=grace)
        except asyncio.CancelledError:  # pragma: no cover - drain task itself cancelled
            raise
    exit_fn(0)


def install_drain_signal_handler(
    loop: Any, on_drain: Callable[[], Any]
) -> None:
    """Register the routine MIG-drain path on SIGTERM (the signal a GCE MIG sends on drain).

    SIGTERM is the drain signal for the ``meeting_runtime``/``code_intel`` GCE-MIG
    deployables; it triggers ``begin_drain`` (set draining → finish in-flight → exit).
    SIGINT stays on the interactive parallel-shutdown path (``install_signal_handlers``).
    """
    loop.add_signal_handler(signal.SIGTERM, on_drain)


def readiness_status(app: Any) -> tuple[int, str]:
    """The (http_status, state) a /readiness probe reports: 503 while draining, else 200."""
    if is_draining(app):
        return 503, "draining"
    return 200, "ready"


def install_readiness_route(app: Any) -> None:
    """Mount GET /readiness on a FastAPI ``app`` (503 while draining, 200 otherwise).

    Registered via ``add_api_route`` (not the ``@app.get`` decorator, so a typed
    handler over an ``Any``-typed app stays strict-clean). The probe is the MIG's
    routing signal, so it mounts OUTSIDE the auth wall (unauthenticated readiness).
    """
    from fastapi.responses import JSONResponse

    init_drain_state(app)

    async def readiness() -> JSONResponse:
        status, state = readiness_status(app)
        return JSONResponse({"status": state}, status_code=status)

    app.add_api_route("/readiness", readiness, methods=["GET"])


def build_meeting_runtime_readiness_app() -> Any:
    """Build the minimal meeting_runtime ASGI app exposing GET /readiness.

    The meeting_runtime deployable's readiness surface: a FastAPI app carrying the
    ``draining`` state + the /readiness probe the GCE MIG polls. In the full harness
    boot this route is mounted (via ``install_readiness_route``) onto the same app the
    routers mount on; this builder is the standalone surface the drain lifecycle owns.
    """
    from fastapi import FastAPI

    app = FastAPI(title="proxy-meeting-runtime")
    install_readiness_route(app)
    return app


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
    #
    # The per-meeting runtime registry is constructed HERE so meeting-join (and the
    # bot provisioner) resolve one MeetingRuntime — its in-process SignalCarrier +
    # its live Scribe notes engine — per meeting. This is the production wiring that
    # makes ``run_scribe`` actually run for a real meeting (DOC03-SCRIBE-PIPELINE).
    from services.harness.src.harness.meeting_runtime import MeetingRuntimeRegistry

    # The close-pass vendor edges (GCS finalized-notes bucket + Recall chat poster +
    # the Sonnet close caller). When configured, the registry runs the ordered close
    # pass on meeting end so the permanent markdown notes record is produced live
    # (gap DOC03-CLOSE-PASS-UNWIRED). Absent config -> bare teardown (dev/no-bucket).
    close_config = _build_close_config(app.state.db)
    app.state.meeting_runtimes = MeetingRuntimeRegistry(
        app.state.db, close_config=close_config
    )
    # Wire the meeting_runtime deployable's provisioner seam so the webhook drain turns a
    # Recall in_call into a RUNNING, atomically-claimed meeting on the real path (§3.6/§3.2).
    # make_provision_launcher's ``launch`` routes an in_call THROUGH the provisioner (atomic
    # claim + one-scope assembly + loop launch) instead of the Scribe-only start; the drain
    # loop below is the ONE production caller that keeps this seam live, not a test-only path.
    from services.harness.src.harness.provisioner import make_provision_launcher

    app.state.meeting_tasks = set()
    # The routine MIG-drain state rides the SAME in-flight meeting-task set the
    # provisioner launches into, so ``begin_drain`` waits on the real live meetings.
    init_drain_state(app)
    app.state.provision_launch = make_provision_launcher(
        app.state.db, app.state.meeting_runtimes, tasks=app.state.meeting_tasks
    )
    _start_webhook_drain(app)
    set_provisioner_ready(gate)


def _build_close_config(db: Any) -> Any | None:
    """Build the real meeting-close vendor config from settings (production wiring).

    Constructs the real GCS finalized-notes bucket handle from ``settings.gcs_bucket``
    and a chat poster that routes the notes link through the real Recall ``post_chat``
    seam (the ONE ``call_external`` funnel). Returns ``None`` when no notes bucket is
    configured (a dev host with no GCS) so meeting end still tears the runtime down.

    The notes URL embeds the meeting id (``gs://<bucket>/meetings/<id>/notes.md``), so
    the registry-level poster resolves the meeting's Recall bot from the URL and posts
    through transport — no per-meeting poster state is needed on the registry.
    """
    from services.harness.src.harness import settings as settings_mod
    from services.harness.src.harness.scribe_runtime import CloseConfig

    cfg = settings_mod.settings
    bucket_name = getattr(cfg, "gcs_bucket", "") or ""
    if not bucket_name:
        return None

    def _bucket() -> Any:
        # Route through the ONE libs.http seam — the sole legitimate home for the
        # raw GCS client construction (§14: no raw external client in services/).
        # The SDK is imported lazily inside the accessor so boot stays offline.
        from libs.http.src.http.external import gcs_bucket

        return gcs_bucket(bucket_name)

    class _LazyBucket:
        """Defers GCS client construction to first blob access (boot stays offline)."""

        def blob(self, name: str) -> Any:
            return _bucket().blob(name)

    async def _post_chat_link(url: str) -> None:
        # Resolve the meeting id embedded in the notes URL, look up its Recall bot,
        # and post the link through the REAL Recall chat seam via call_external.
        import re

        from transport.recall import RecallTransport

        from libs.db import repos
        from libs.http.src.http.external import call_external as real_call_external

        m = re.search(r"/meetings/([^/]+)/notes\.md$", url)
        if m is None:
            return
        meeting_id = m.group(1)
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT recall_bot_id FROM meetings WHERE id = $1::uuid", meeting_id
            )
        bot_id = row and row["recall_bot_id"]
        if not bot_id:
            return
        transport = RecallTransport(real_call_external, api_key=cfg.recall_api_key)
        await transport.post_chat(str(bot_id), f"Meeting notes: {url}", pinned=True)
        _ = repos  # repos import kept for parity with the sibling resolve paths

    return CloseConfig(
        bucket=_LazyBucket(),
        bucket_name=bucket_name,
        post_chat_link=_post_chat_link,
    )


# The interval between periodic webhook drains (an in_call that raced boot, or a
# redelivery, is picked up within this window). unit: seconds.
WEBHOOK_DRAIN_INTERVAL_S: float = 2.0


async def _drain_webhooks_forever(app: Any) -> None:
    """Boot + periodically drain pending webhooks through the provisioner seam.

    This is the ONE production caller of ``drain_pending_webhooks`` on the meeting_runtime
    deployable: it drains once immediately (catching an in_call durably landed before boot
    finished) and then on ``WEBHOOK_DRAIN_INTERVAL_S`` forever, routing each in_call THROUGH
    ``app.state.provision_launch`` (atomic claim + one-scope assembly + loop launch, §3.6/§3.2)
    so the provisioner is live on the real path — not a test-only island. A drain fault is
    logged and the loop continues (a poison row never stalls the whole queue).
    """
    from services.harness.src.harness.webhooks import drain_pending_webhooks

    while True:
        try:
            await drain_pending_webhooks(
                app.state.db,
                registry=app.state.meeting_runtimes,
                launch=app.state.provision_launch,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a drain fault must never kill the loop
            _log.error("webhook_drain_error", error=str(exc))
        await asyncio.sleep(WEBHOOK_DRAIN_INTERVAL_S)


def _start_webhook_drain(app: Any) -> None:
    """Launch the periodic webhook-drain loop as a supervised background task.

    Stashes the task on ``app.state.webhook_drain_task`` so ``_shutdown_real`` can cancel it
    on teardown; a strong reference on ``app.state`` keeps it from being GC'd mid-flight.
    """
    app.state.webhook_drain_task = asyncio.ensure_future(_drain_webhooks_forever(app))


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
    (routers strictly after the reaper) is owned here. The unauthenticated GET
    /readiness probe (the MIG's routing signal — 503 while draining) mounts here too,
    OUTSIDE the auth wall, strictly after the reaper alongside the routers.
    """
    if hasattr(app, "get"):
        install_readiness_route(app)
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
    drain_task = getattr(app.state, "webhook_drain_task", None)
    if drain_task is not None:
        drain_task.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError, Exception):
            await drain_task
    db = getattr(app.state, "db", None)
    if db is not None:
        await db.close()
