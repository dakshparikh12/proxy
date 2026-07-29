"""Doc 00 · §6 MIG-drain graceful shutdown + /readiness probe (AC-BOOT-006 "Additionally").

Milestone m04, the routine-drain clause the core-boot suite (``test_m04_boot.py``)
does not cover. §6 (spec lines 37/97/206) mandates, for the two GCE-MIG deployables
``meeting_runtime`` and ``code_intel``:

  * a ``draining`` state on the app (``app.state.draining``);
  * a **routine MIG-drain path** — SIGTERM sets ``draining`` then lets in-flight
    meetings finish within a real grace window (minutes, not Cloud Run's 10s) before
    the process exits. This is DISTINCT from the §5 heartbeat/reclaim exception
    (defense-in-depth for a rare hard drain), which the core suite already covers;
  * a GET ``/readiness`` probe that returns **503 while draining** (so the MIG stops
    routing new meetings to a shutting-down instance) and **200 otherwise**, on BOTH
    the ``meeting_runtime`` (harness) app and the ``code_intel`` app.

Oracle sources (all deterministic/hermetic — no DB, no network):

  * [integration] the harness ``server`` draining lifecycle: ``init_drain_state`` /
    the SIGTERM-registered ``begin_drain`` set the flag, wait for in-flight meeting
    tasks, then invoke the exit hook. A slow in-flight task proves finish-within-grace.
  * [integration] the two ``/readiness`` routes, driven over the real ASGI apps with a
    Starlette ``TestClient``: 200 → set draining → 503, on meeting_runtime + code_intel.

Product code is imported INSIDE each test body (never at module top level), so this
module COLLECTS clean and FAILS red before the product path exists.
"""

import asyncio
import signal

import pytest


# ══════════════════════════════════════════════════════════════════════════
# /readiness == 200 normally, 503 while draining — meeting_runtime (harness) app
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_readiness_200_then_503_on_meeting_runtime_app():
    """The meeting_runtime app's GET /readiness returns 200 normally and 503 once draining is set."""
    from starlette.testclient import TestClient

    from services.control_plane import server

    app = server.build_meeting_runtime_readiness_app()

    # A fresh instance is ready → 200 (the MIG routes meetings to it).
    with TestClient(app) as client:
        resp = client.get("/readiness")
        assert resp.status_code == 200, (
            f"a non-draining meeting_runtime instance must report ready (200); got {resp.status_code}"
        )

        # Flip the draining flag (as the SIGTERM/MIG-drain path does) → 503.
        server.set_draining(app, True)
        resp = client.get("/readiness")
        assert resp.status_code == 503, (
            "a draining meeting_runtime instance must report NOT-ready (503) so the MIG "
            f"stops routing new meetings to it; got {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════
# /readiness == 200 normally, 503 while draining — code_intel app
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_readiness_200_then_503_on_code_intel_app():
    """The code_intel app's GET /readiness returns 200 normally and 503 once draining is set."""
    from starlette.testclient import TestClient

    from code_intel.readiness_app import build_code_intel_readiness_app, set_draining

    app = build_code_intel_readiness_app()

    with TestClient(app) as client:
        resp = client.get("/readiness")
        assert resp.status_code == 200, (
            f"a non-draining code_intel host must report ready (200); got {resp.status_code}"
        )

        set_draining(app, True)
        resp = client.get("/readiness")
        assert resp.status_code == 503, (
            "a draining code_intel host must report NOT-ready (503); got {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════
# SIGTERM registration wires the routine MIG-drain path (distinct from SIGINT)
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_sigterm_registered_for_mig_drain():
    """SIGTERM is registered to the routine MIG-drain path (set draining → finish in-flight → exit)."""
    from services.control_plane import server

    registered: dict[int, object] = {}

    class _FakeLoop:
        def add_signal_handler(self, sig, cb):  # noqa: ANN001
            registered[int(sig)] = cb

    # The drain-signal installer must wire SIGTERM (the MIG drain signal) to a handler.
    server.install_drain_signal_handler(_FakeLoop(), lambda: None)
    assert int(signal.SIGTERM) in registered, (
        "the routine MIG-drain path must be registered on SIGTERM "
        "(the signal a GCE MIG sends on drain)"
    )


# ══════════════════════════════════════════════════════════════════════════
# begin_drain: set draining → in-flight finishes within grace → exit hook fires
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_begin_drain_lets_inflight_finish_within_grace_then_exits():
    """begin_drain sets draining, waits for an in-flight meeting to finish within the grace window, then exits."""
    from services.control_plane import server

    async def _drive():
        app = server.build_meeting_runtime_readiness_app()

        # Seed an in-flight meeting task that finishes partway through the grace window.
        finished: list[str] = []

        async def _inflight_meeting():
            await asyncio.sleep(0.05)  # a live meeting still wrapping up
            finished.append("meeting-done")

        task = asyncio.ensure_future(_inflight_meeting())
        server.register_inflight(app, task)

        exited: list[int] = []

        def _exit(code: int = 0) -> None:
            exited.append(code)

        # Not draining yet.
        assert server.is_draining(app) is False

        # The routine MIG-drain: set draining, let the in-flight meeting finish
        # inside a generous grace window, then exit cleanly (never hard-kill it).
        await server.begin_drain(app, grace_s=5.0, exit_fn=_exit)

        # 1. draining was set (so /readiness answered 503 during the wait).
        assert server.is_draining(app) is True, "begin_drain must set the draining flag first"
        # 2. the in-flight meeting was allowed to COMPLETE (not cut off mid-flight).
        assert finished == ["meeting-done"], (
            "begin_drain must let the in-flight meeting finish within the grace window "
            f"before exit (routine MIG drain, not a hard reclaim); finished={finished}"
        )
        assert task.done() and task.exception() is None
        # 3. the process exit hook fired exactly once, cleanly (code 0), AFTER the meeting ended.
        assert exited == [0], f"begin_drain must exit cleanly once the meeting finished; exited={exited}"

    asyncio.run(_drive())


# ══════════════════════════════════════════════════════════════════════════
# begin_drain: a meeting that OUTLASTS the grace window is bounded (still exits)
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_begin_drain_bounded_by_grace_window():
    """begin_drain exits when the grace window elapses even if an in-flight meeting overruns (never hangs forever)."""
    from services.control_plane import server

    async def _drive():
        app = server.build_meeting_runtime_readiness_app()

        async def _overrunning_meeting():
            await asyncio.sleep(10.0)  # a meeting that overruns the tiny grace window

        task = asyncio.ensure_future(_overrunning_meeting())
        server.register_inflight(app, task)
        exited: list[int] = []

        await server.begin_drain(app, grace_s=0.05, exit_fn=lambda code=0: exited.append(code))

        assert server.is_draining(app) is True
        # The drain is bounded: it exits at the grace deadline rather than hanging on the overrun.
        assert exited, "begin_drain must exit at the grace deadline even if a meeting overruns"
        task.cancel()

    asyncio.run(_drive())
